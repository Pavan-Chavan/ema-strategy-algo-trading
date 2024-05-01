import os
import utils
import time
import heroku

from dotenv import load_dotenv
from constants import Holding, Trade, Env
from strategy.entry import get_analyzed_params
from orders import orders
from kite_utils import kite_utils
from datetime import datetime as dt
from db import mongodb
from mail.app import Mail


load_dotenv()


def dump_candle_data(candle_data):
    mongodb.MongoDB.candle_collection.insert_one(candle_data)


def dump_holding_data(entry_details):
    mongodb.MongoDB.holding_cache = None
    mongodb.MongoDB.holding_collection.insert_one(entry_details)
    Mail.send_entry_email(entry_details)


def dump_trade_data(exit_details):
    acknowledged = mongodb.MongoDB.trade_collection.insert_one(
        exit_details
    ).acknowledged
    mongodb.MongoDB.holding_cache = None
    if acknowledged:
        mongodb.MongoDB.holding_collection.delete_one(
            {Holding.SYMBOL: exit_details[Trade.SYMBOL]}
        )
        Mail.send_exit_email(exit_details)


def start_trading():
    symbol = os.environ[Env.SYMBOL]
    exchange = os.environ[Env.EXCHANGE]

    is_trading_started_mail_sent = False
    while utils.get_market_status()["open"]:
        if not utils.is_trading_time():
            continue

        if not is_trading_started_mail_sent:
            Mail.send_trading_started_email()
            is_trading_started_mail_sent = True

        now = dt.now()

        # Run for every 5 minute (5 minute candle)
        if now.minute % 5 == 0 and now.second == 0:
            if kite_utils.get_holding(symbol):
                exit_details = orders.exit_order(exchange, symbol)
                if exit_details:
                    dump_trade_data(exit_details)
            else:
                entry_details = orders.entry_order(exchange, symbol)
                if entry_details:
                    dump_holding_data(entry_details)
            dump_candle_data(get_analyzed_params(exchange, symbol))
            print(now.strftime("%Y-%m-%d %H:%M:%S") + " - Candle data dumped...")
            heroku.activate_dyno()
            time.sleep(1)

    print("Market is closed due to: " + utils.get_market_status()["reason"])
    Mail.send_market_close_email(utils.get_market_status()["reason"])
