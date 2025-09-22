"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""
import json
from typing import List, Dict, Optional

class Cart:
    def __init__(self):
        self.items = {}  # (asin, options_key) -> {quantity, options, image, selected}
    def to_dict(self):
        """Convert cart items to a dictionary for JSON serialization"""
        cart_data = []
        for (asin, options_key), item in self.items.items():
            cart_data.append({
                'asin': asin,
                'options': json.loads(options_key),
                'quantity': item['quantity'],
                'image': item.get('image')
            })
        return cart_data
    @classmethod
    def from_dict(cls, cart_data: List[Dict]):
        """Load cart items from a dictionary"""
        cart = cls()
        for item in cart_data:
            asin = item['asin']
            options = item['options']
            quantity = item['quantity']
            image = item.get('image')
            cart.add_item(asin, options, quantity, image)
        return cart
    def _get_options_key(self, options: dict) -> str:
        """Convert options dict to a stable string key"""
        return json.dumps(options, sort_keys=True)
        
    def add_item(self, asin: str, options: Dict, quantity: int = 1, image: str = None):
        options_key = json.dumps(options, sort_keys=True)
        item_key = (asin, options_key)
        
        if item_key in self.items:
            self.items[item_key]['quantity'] += quantity
        else:
            self.items[item_key] = {
                'quantity': quantity,
                'options': options,
                'selected': True,
                'image': image
            }

    def remove_item(self, asin: str, options: dict = None, quantity: int = None):
        if options is None:
            # Remove all items with this asin
            keys_to_remove = [(k1, k2) for k1, k2 in self.items.keys() if k1 == asin]
            for key in keys_to_remove:
                del self.items[key]
        else:
            # Remove specific item with options
            cart_key = (asin, self._get_options_key(options))
            if cart_key in self.items:
                if quantity is None or quantity >= self.items[cart_key]["quantity"]:
                    del self.items[cart_key]
                else:
                    self.items[cart_key]["quantity"] -= quantity

    def toggle_item_selection(self, asin: str, options: Dict) -> bool:
        """Toggle selection status for an item and return new status"""
        options_key = self._get_options_key(options)
        item_key = (asin, options_key)
        
        if item_key in self.items:
            self.items[item_key]['selected'] = not self.items[item_key].get('selected', True)
            return self.items[item_key]['selected']
        return False
            
    def get_selected_total(self, product_prices):
        """Return the total price of selected items in cart"""
        return sum(product_prices[asin] * item["quantity"] 
                   for (asin, _), item in self.items.items() if item["selected"])
    
    def get_total(self, product_prices):
        return sum(product_prices[asin] * item["quantity"] 
                  for (asin, _), item in self.items.items())

    def get_total_quantity(self) -> int:
        """Return the total quantity of all items in cart"""
        return sum(item["quantity"] for item in self.items.values())