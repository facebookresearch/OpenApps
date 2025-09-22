"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""
import ast  # Import the ast module for safely evaluating string representations of Python literals
import json
from typing import List, Dict, Optional
class Order:
    def __init__(self, order_id: str, items: dict, total: float, name: str, address: str, 
                 date: str, status: str = "Processing"):
        self.order_id = order_id
        self.items = items  # Same structure as cart items
        self.total = total
        self.name = name
        self.address = address
        self.date = date
        self.status = status
    def to_dict(self):
        """Convert order to a dictionary for JSON serialization"""
        # Convert tuple keys in items dictionary to strings for JSON compatibility
        string_keyed_items = {str(k): v for k, v in self.items.items()}
        return {
            'order_id': self.order_id,
            'items': string_keyed_items, # Use the dictionary with string keys
            'total': self.total,
            'name': self.name,
            'address': self.address,
            'date': self.date,
            'status': self.status
        }
    @classmethod
    def from_dict(cls, order_data: Dict):
        """Load order from a dictionary, converting string keys back to tuples."""
        order_id = order_data['order_id']
        items_from_data = order_data.get('items', {})
        tuple_keyed_items = {}
        if isinstance(items_from_data, dict):
            for k_str, v in items_from_data.items():
                try:
                    # Safely evaluate the string representation of the key
                    # Check if it looks like a tuple string before evaluating
                    if k_str.startswith('(') and k_str.endswith(')'):
                        k_evaluated = ast.literal_eval(k_str)
                        # Check if the evaluated key is actually a tuple
                        if isinstance(k_evaluated, tuple):
                            tuple_keyed_items[k_evaluated] = v
                        else:
                            # If not a tuple after evaluation, keep the original string key (less likely with the check)
                            tuple_keyed_items[k_str] = v
                    else:
                         # If the string doesn't look like a tuple string, keep it as is
                         tuple_keyed_items[k_str] = v
                except (ValueError, SyntaxError):
                    # If literal_eval fails, keep the original string key
                    tuple_keyed_items[k_str] = v

        return cls(
            order_id=order_id,
            items=tuple_keyed_items,
            total=order_data['total'],
            name=order_data['name'],
            address=order_data['address'],
            date=order_data['date'],
            status=order_data['status'],
        )