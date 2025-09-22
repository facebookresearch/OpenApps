"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""
from fastapi import APIRouter, Form
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from ..templates.html_generator import generate_base_html
from ..models.global_state import global_state
from typing import List, Dict, Optional
from datetime import datetime
import uuid
from ..models.order import Order

router = APIRouter()


@router.get("/orders", response_class=HTMLResponse)
async def order_history():
    orders_html = ""
    
    for order in reversed(global_state.orders):
        items_html = ""
        for (asin, options_key), item in order.items.items():
            product = global_state.product_item_dict[asin]
            display_image = item.get("image") or product['MainImage']
            price_per_item = global_state.product_prices[asin]
            total_item_price = price_per_item * item['quantity']
            
            options_html = ""
            if item["options"]:
                options_html = "<div class='text-muted small'>"
                for option_name, option_value in item["options"].items():
                    options_html += f"<div>{option_name}: {option_value}</div>"
                options_html += "</div>"
                
            items_html += f"""
                <div class="d-flex align-items-center mb-3 border-bottom pb-3">
                    <a href="/onlineshop/item/{asin}" class="d-flex text-decoration-none text-dark flex-grow-1">
                        <img src="{display_image}" class="img-thumbnail me-3" style="width: 60px;">
                        <div class="flex-grow-1">
                            <div class="fw-bold">{product['Title'][:50]}...</div>
                            {options_html}
                            <div class="text-muted small">
                                Quantity: {item['quantity']} × ${price_per_item:.2f}
                            </div>
                        </div>
                        <div class="ms-3 text-end">
                            <div class="fw-bold">${total_item_price:.2f}</div>
                        </div>
                    </a>
                </div>
            """
            
        orders_html += f"""
            <div class="card mb-4">
                <div class="card-header d-flex justify-content-between align-items-center">
                    <div>
                        <h5 class="mb-0">Order #{order.order_id}</h5>
                        <div class="text-muted small">Placed on {order.date}</div>
                    </div>
                    <span class="badge bg-primary">{order.status}</span>
                </div>
                <div class="card-body">
                    <div class="row">
                        <div class="col-md-8">
                            <h6>Items:</h6>
                            {items_html}
                        </div>
                        <div class="col-md-4">
                            <div class="card">
                                <div class="card-body">
                                    <h6>Order Details</h6>
                                    <p class="mb-1"><strong>Total:</strong> ${order.total:.2f}</p>
                                    <hr>
                                    <p class="mb-1"><strong>Shipping to:</strong><br>{order.name}<br>{order.address}</p>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        """
    
    return generate_base_html(f"""
        <div class="container py-5">
            <h2 class="mb-4">Order History</h2>
            {orders_html if global_state.orders else 
             '<div class="alert alert-info">No orders found. Start shopping!</div>'}
            <div class="mt-3">
                <a href="/onlineshop" class="btn btn-primary">Continue Shopping</a>
            </div>
        </div>
    """)

@router.post("/checkout")
async def checkout(name: str = Form(...), address: str = Form(...), card: str = Form(...)):
    # Create new cart for selected items
    selected_items = {
        key: item for key, item in global_state.cart.items.items() 
        if item["selected"]
    }
    
    if not selected_items:
        return RedirectResponse(url="/onlineshop/cart", status_code=303)
        
    total_amount = global_state.cart.get_selected_total(global_state.product_prices)
    
    # Create new order for selected items only
    order_id = str(uuid.uuid4())[:8]
    order = Order(
        order_id=order_id,
        items=selected_items,
        total=total_amount,
        name=name,
        address=address,
        date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    # Add to order history
    global_state.orders.append(order)
    
    # Remove selected items from cart
    for (asin, options_key) in selected_items.keys():
        global_state.cart.remove_item(asin, selected_items[(asin, options_key)]["options"])

    # Save the orders and cart to a local database
    global_state.save_orders()
    global_state.save_cart()
    
    return RedirectResponse(url=f"/onlineshop/order-confirmation?orderid={order_id}", status_code=303)

@router.get("/order-confirmation", response_class=HTMLResponse)
async def order_confirmation(orderid: str):
    return generate_base_html(f"""
        <div class="container py-5 text-center">
            <h2>Thank you for your purchase!</h2>
            <p>Your order has been confirmed and will be shipped soon.</p>
            <p>Order confirmation number: {orderid}</p>
            <div class="mt-4">
                <a href="/onlineshop" class="btn btn-primary">Continue Shopping</a>
            </div>
        </div>
    """)