import time
from typing import Dict, Tuple

import pandas as pd
from hummingbot.client.config.client_config_map import ClientConfigMap
from hummingbot.client.config.config_helpers import ClientConfigAdapter, get_connector_class
from hummingbot.client.settings import AllConnectorSettings, ConnectorType
from hummingbot.data_feed.candles_feed.candles_factory import CandlesFactory
from hummingbot.data_feed.candles_feed.data_types import CandlesConfig, HistoricalCandlesConfig

from data_handler.ohlc import OHLC
from data_handler.trading_rules import TradingRules


class ClobDataSource:
    CONNECTOR_TYPES = [ConnectorType.CLOB_SPOT, ConnectorType.CLOB_PERP, ConnectorType.Exchange,
                       ConnectorType.Derivative]
    EXCLUDED_CONNECTORS = ["vega_perpetual", "hyperliquid_perpetual", "dydx_perpetual", "cube",
                           "polkadex", "coinbase_advanced_trade", "kraken"]

    def __init__(self):
        self.candles_factory = CandlesFactory()
        self.conn_settings = AllConnectorSettings.get_connector_settings()
        self.connectors = {name: self.get_connector(name) for name, settings in self.conn_settings.items()
                           if settings.type in self.CONNECTOR_TYPES and name not in self.EXCLUDED_CONNECTORS and
                           "testnet" not in name}
        self.candles_cache: Dict[Tuple[str, str, str], pd.DataFrame] = {}

    @staticmethod
    def get_connector_config_map(connector_name: str):
        connector_config = AllConnectorSettings.get_connector_config_keys(connector_name)
        return {key: "" for key in connector_config.__fields__.keys() if key != "connector"}

    async def get_candles(self,
                          connector_name: str,
                          trading_pair: str,
                          interval: str,
                          start_time: int,
                          end_time: int) -> OHLC:
        cache_key = (connector_name, trading_pair, interval)

        if cache_key in self.candles_cache:
            cached_df = self.candles_cache[cache_key]
            cached_start_time = cached_df.index.min().timestamp()
            cached_end_time = cached_df.index.max().timestamp()

            if cached_start_time <= start_time and cached_end_time >= end_time:
                return OHLC(cached_df[(cached_df.index >= pd.to_datetime(start_time, unit='s')) &
                                      (cached_df.index <= pd.to_datetime(end_time, unit='s'))])
            else:
                if start_time < cached_start_time:
                    new_start_time = start_time
                    new_end_time = cached_start_time - 1
                else:
                    new_start_time = cached_end_time + 1
                    new_end_time = end_time
        else:
            new_start_time = start_time
            new_end_time = end_time

        candle = self.candles_factory.get_candle(CandlesConfig(
            connector=connector_name,
            trading_pair=trading_pair,
            interval=interval
        ))
        candles_df = await candle.get_historical_candles(HistoricalCandlesConfig(
            connector_name=connector_name,
            trading_pair=trading_pair,
            start_time=new_start_time,
            end_time=new_end_time,
            interval=interval
        ))
        candles_df.index = pd.to_datetime(candles_df.timestamp, unit='s')

        if cache_key in self.candles_cache:
            self.candles_cache[cache_key] = pd.concat(
                [self.candles_cache[cache_key], candles_df]).drop_duplicates().sort_index()
        else:
            self.candles_cache[cache_key] = candles_df

        return OHLC(self.candles_cache[cache_key][
                        (self.candles_cache[cache_key].index >= pd.to_datetime(start_time, unit='s')) &
                        (self.candles_cache[cache_key].index <= pd.to_datetime(end_time, unit='s'))])

    async def get_candles_last_days(self,
                                    connector_name: str,
                                    trading_pair: str,
                                    interval: str,
                                    days: int) -> OHLC:
        end_time = int(time.time())
        start_time = end_time - days * 24 * 60 * 60
        return await self.get_candles(connector_name, trading_pair, interval, start_time, end_time)

    def get_connector(self, connector_name: str):
        conn_setting = self.conn_settings.get(connector_name)
        if conn_setting is None:
            raise Exception(f"Connector {connector_name} not found")
        client_config_map = ClientConfigAdapter(ClientConfigMap())
        init_params = conn_setting.conn_init_parameters(
            trading_pairs=[],
            trading_required=False,
            api_keys=self.get_connector_config_map(connector_name),
            client_config_map=client_config_map,
        )
        connector_class = get_connector_class(connector_name)
        connector = connector_class(**init_params)
        return connector

    async def get_trading_rules(self, connector_name: str):
        connector = self.connectors.get(connector_name)
        exchange_info = await connector._make_trading_rules_request()
        trading_rules_list = await connector._format_trading_rules(exchange_info)
        return TradingRules(trading_rules_list)
