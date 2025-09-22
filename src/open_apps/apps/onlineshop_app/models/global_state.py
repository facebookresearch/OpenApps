"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""
from omegaconf import OmegaConf, DictConfig, ListConfig
from .cart import Cart
from .order import Order
from ..engine.engine import DEFAULT_FILE_PATH, DEBUG_PROD_SIZE
from ..engine.goal import get_goals
from typing import List, Dict, Optional
import json
class GlobalState:
    def __init__(self):
        self.search_engine = None
        self.all_products = None
        self.product_item_dict = None
        self.product_prices = None
        self.attribute_to_asins = None
        self.SHOW_ATTRS_TAB = False
        self.cart = Cart()
        self.config = None
        self.orders = []

    def initialize(self):
        from ..engine.engine import load_products, init_search_engine
        (self.all_products, self.product_item_dict, 
         self.product_prices, self.attribute_to_asins) = load_products(
            filepath=DEFAULT_FILE_PATH,
            num_products=DEBUG_PROD_SIZE
        )
        self.search_engine = init_search_engine(num_products=DEBUG_PROD_SIZE)
    def update_config(self, config):
        self.config = config
    def load_state_from_config(self):
        cart, orders = self.config.cart, self.config.orders
        if isinstance(cart, ListConfig) or isinstance(cart, DictConfig):
            cart = OmegaConf.to_container(cart, resolve=True)
        if isinstance(orders, ListConfig) or isinstance(orders, DictConfig):
            orders = OmegaConf.to_container(orders, resolve=True)
        self.cart = Cart.from_dict(cart) if cart else Cart()
        self.orders = [Order.from_dict(order) for order in orders] if orders else []
        self.save_cart()
        self.save_orders()
    def save_orders(self, path=None):
        if path is None:
            path = self.config.database_path + '/orders.json'
        with open(path, 'w') as f:
            json.dump([order.to_dict() for order in self.orders], f, indent=4)
    def load_orders(self, path=None):
        if path is None:
            path = self.config.database_path + '/orders.json'
        try:
            with open(path, 'r') as f:
                for order in json.load(f):
                    self.orders.append(Order.from_dict(order))
        except Exception as e:
            self.orders = []
    def load_cart(self, path=None):
        if path is None:
            path = self.config.database_path + '/cart.json'
        try:
            with open(path, 'r') as f:
                self.cart = Cart.from_dict(json.load(f))
        except Exception as e:
            self.cart = Cart()
    def save_cart(self, path=None):
        if path is None:
            path = self.config.database_path + '/cart.json'
        with open(path, 'w') as f:
            json.dump(self.cart.to_dict(), f, indent=4)
global_state = GlobalState()