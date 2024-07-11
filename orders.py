import strategy
import time
import os

from utils import kite_utils as ku
from constants import LogType, Env
from connection import kite
from db import MongoDB
from datetime import datetime as dt, timedelta as td
from mail import app as mail_app


def place_entry_order(order_details, holding, instrument_token):
    tran_type = order_details["transaction_type"]
    now = dt.now()
    wait_time = int(os.environ[Env.ENTRY_TIME_FRAME]) * 2.8
    end_minutes = (wait_time * 10) // 10
    end_seconds = (wait_time * 10) % 10
    wait_seconds = end_minutes * 60
    valid_till = now + td(minutes=end_minutes, seconds=end_seconds)

    while now < valid_till:
        ohlc = ku.get_ohlc(instrument_token)
        if (
            tran_type == kite.TRANSACTION_TYPE_BUY
            and ohlc["high"] > order_details["price"]
        ):
            order_details["price"] = holding["entry_price"] = ohlc["high"]
            break
        elif (
            tran_type == kite.TRANSACTION_TYPE_SELL
            and ohlc["low"] < order_details["price"]
        ):
            order_details["price"] = holding["entry_price"] = ohlc["low"]
            break
        now = dt.now()
        wait_seconds -= 1
        time.sleep(1)
    if now >= valid_till:
        msg = "Order failed"
        details = {"status": "Candle high/low not break", **holding}
        mail_app.send_order_status_email(details, msg)
        MongoDB.insert_log(
            log_type=LogType.FAIL,
            message=msg,
            details=details,
        )
        return
    order_details["validity_ttl"] = int((abs(wait_seconds) // 60) + 1)
    order_id = kite.place_order(**order_details)
    msg = "Order placed successfully"
    details = {"order_id": order_id, **holding}
    MongoDB.insert_log(log_type=LogType.SUCCESS, message=msg, details=details)
    mail_app.send_order_status_email(details, msg)

    while ku.get_order_status(order_id)["status"] not in (
        kite.STATUS_COMPLETE,
        kite.STATUS_REJECTED,
        kite.STATUS_CANCELLED,
    ):
        time.sleep(10)

    details = {"order_id": order_id, **holding}
    if ku.get_order_status(order_id)["status"] == kite.STATUS_COMPLETE:
        MongoDB.holdings.insert_one(holding)
        msg = "Order executed successfully"
        MongoDB.insert_log(
            log_type=LogType.SUCCESS,
            message=msg,
            details=details,
        )
        mail_app.send_order_status_email(details, msg)
    else:
        msg = "Order failed"
        MongoDB.insert_log(
            log_type=LogType.FAIL,
            message=msg,
            details=details,
        )
        mail_app.send_order_status_email(details, msg)


def search_entry(symbol_details):
    symbol, exchange = symbol_details["tradingsymbol"], symbol_details["exchange"]
    instrument_token = symbol_details["instrument_token"]
    time_frame = int(os.environ[Env.ENTRY_TIME_FRAME])
    ohlc = ku.get_historical_data(
        instrument_token, ku.get_candle_interval(time_frame), int(time_frame)
    )

    signal_details = strategy.get_entry_signal(ohlc)
    MongoDB.insert_log(
        log_type=LogType.TRADE,
        message="Searching for entry",
        details={"symbol": symbol, "exchange": exchange, **signal_details},
    )
    if not signal_details["signal"]:
        return

    holding = {
        "symbol": symbol,
        "exchange": exchange,
        "from": str(ohlc[-1]["date"]),
        "product": symbol_details["params"]["product"],
        "quantity": symbol_details["params"]["quantity"],
        "ltp": ohlc[-1]["close"],
        **signal_details,
    }

    if signal_details["signal"] == kite.TRANSACTION_TYPE_BUY:
        holding["entry_price"] = ohlc[-1]["high"]
    else:
        holding["entry_price"] = ohlc[-1]["low"]

    order_detail = {
        "tradingsymbol": symbol,
        "exchange": exchange,
        "product": holding["product"],
        "variety": kite.VARIETY_REGULAR,
        "transaction_type": signal_details["signal"],
        "quantity": holding["quantity"],
        "order_type": kite.ORDER_TYPE_LIMIT,
        "price": holding["entry_price"],
        "validity": kite.VALIDITY_TTL,
    }

    place_entry_order(order_detail, holding, instrument_token)


def search_exit(holding):
    time_frame = int(os.environ[Env.EXIT_TIME_FRAME])
    ohlc = ku.get_historical_data(
        holding["instrument_token"], ku.get_candle_interval(time_frame), int(time_frame)
    )
    signal = strategy.get_exit_signal(ohlc)
    MongoDB.insert_log(
        log_type=LogType.TRADE,
        message="Searching for exit",
        details={
            "symbol": holding["tradingsymbol"],
            "exchange": holding["exchange"],
            "quantity": holding["quantity"],
            "ltp": ohlc[-1]["close"],
            "exit_signal": signal,
        },
    )
    MongoDB.holdings.update_many(
        {"symbol": holding["tradingsymbol"]}, {"$set": {"ltp": ohlc[-1]["close"]}}
    )
    if not signal:
        return
    if (
        signal == kite.TRANSACTION_TYPE_BUY and holding["quantity"] >= 0
    ):  # If we have bought and signal is to exit buy position
        return
    if (
        signal == kite.TRANSACTION_TYPE_SELL and holding["quantity"] <= 0
    ):  # If we have sold and signal is to exit sell position
        return

    order_id = kite.place_order(
        tradingsymbol=holding["tradingsymbol"],
        exchange=holding["exchange"],
        product=holding["product"],
        variety=kite.VARIETY_REGULAR,
        transaction_type=signal,
        quantity=abs(holding["quantity"]),
        order_type=kite.ORDER_TYPE_MARKET,
        validity=kite.VALIDITY_DAY,
    )

    holding["to"] = str(ohlc[-1]["date"])

    details = {"order_id": order_id, **holding}
    if ku.get_order_status(str(order_id))["status"] == kite.STATUS_COMPLETE:
        entry_signal_details = MongoDB.holdings.find_one(
            {"symbol": holding["tradingsymbol"]}
        )
        holding.update(entry_signal_details)
        MongoDB.holdings.delete_one({"symbol": holding["tradingsymbol"]})
        MongoDB.trades.insert_one(holding)
        msg = "Order executed successfully"
        MongoDB.insert_log(
            log_type=LogType.SUCCESS,
            message=msg,
            details=details,
        )
        mail_app.send_order_status_email(details, msg)
    else:
        msg = "Order failed"
        MongoDB.insert_log(
            log_type=LogType.FAIL,
            message=msg,
            details=details,
        )
        mail_app.send_order_status_email(details, msg)
