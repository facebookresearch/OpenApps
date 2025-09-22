"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""
from fastapi import APIRouter
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from ..templates.html_generator import generate_base_html
from ..models.global_state import global_state
from typing import List, Dict, Optional
import json
from fastapi.responses import JSONResponse

router = APIRouter()

CARD_ICONS = {
    "visa": "https://cdn-icons-png.flaticon.com/32/349/349221.png",
    "amex": "https://cdn-icons-png.flaticon.com/32/349/349228.png",
    "discover": "https://cdn-icons-png.flaticon.com/32/349/349230.png",
    "mastercard": "https://cdn-icons-png.flaticon.com/32/349/349232.png"
}

@router.get("/cart", response_class=HTMLResponse)
async def view_cart():
    cart_items_html = ""
    total = global_state.cart.get_selected_total(global_state.product_prices)
    cart_is_empty = len(global_state.cart.items) == 0
    
    if cart_is_empty:
        return generate_base_html("""
            <div class="container py-5">
                <h2>Shopping Cart</h2>
                <div class="alert alert-info">
                    Your cart is empty. <a href="/onlineshop">Start shopping!</a>
                </div>
                <div class="mt-3">
                    <a href="/onlineshop" class="btn btn-primary">Continue Shopping</a>
                </div>
            </div>
        """)
    
    for (asin, options_key), item in global_state.cart.items.items():
        product = global_state.product_item_dict[asin]
        display_image = item.get("image") or product['MainImage']
        price = global_state.product_prices[asin] * item['quantity']
        is_selected = item.get("selected", True)
        
        options_html = ""
        if item["options"]:
            options_html = "<div class='text-muted'>"
            for option_name, option_value in item["options"].items():
                options_html += f"<div>{option_name}: {option_value}</div>"
            options_html += "</div>"
        
        options_key_escape = options_key.replace("\"", "&quot;")
        options_key_html = options_key
        # options_json = json.dumps(item['options']).replace("'", "\\'").replace('"', '\\"')
        
                
        cart_items_html += f"""
            <div class="card mb-3">
                <div class="row g-0">
                    <div class="col-md-2">
                        <div class="p-2">
                            <div class="form-check">
                                <input class="form-check-input" type="checkbox" 
                                id="item-{asin}-{options_key_escape}"
                                data-asin="{asin}"
                                data-options="{options_key_escape}"
                                onchange="toggleItemSelection(this)"
                                {'checked' if is_selected else ''}>
                                <img src="{display_image}" class="img-fluid rounded-start" alt="{product['Title']}">
                            </div>
                        </div>
                    </div>
                    <div class="col-md-8">
                        <div class="card-body">
                            <h5 class="card-title">{product['Title']}</h5>
                            {options_html}
                            <div class="row align-items-center">
                                <div class="col-auto">
                                    <p class="card-text mb-0">Quantity: {item['quantity']}</p>
                                </div>
                                <div class="col-auto">
                                    <form action="/onlineshop/update-cart/{asin}" method="POST" class="d-inline">
                                        <input type="hidden" name="options" value='{options_key_html}'> 
                                        <div class="input-group input-group-sm" style="width: 120px;">
                                            <button class="btn btn-outline-secondary" type="submit" name="action" value="decrease">-</button>
                                            <input type="number" class="form-control text-center" value="{item['quantity']}" readonly>
                                            <button class="btn btn-outline-secondary" type="submit" name="action" value="increase">+</button>
                                        </div>
                                    </form>
                                </div>
                                <div class="col">
                                    <p class="card-text mb-0">Price: ${price:.2f}</p>
                                </div>
                            </div>
                            <form action="/onlineshop/remove-from-cart/{asin}" method="POST" class="mt-2">
                                <input type="hidden" name="options" value='{options_key_html}'>
                                <button type="submit" class="btn btn-danger btn-sm">Remove</button>
                            </form>
                        </div>
                    </div>
                </div>
            </div>
        """
    
    card_icons_html = ""
    for card_type in global_state.config.allowed_credit_cards:
        if card_type.lower() in CARD_ICONS:
            card_icons_html += f'<img src="{CARD_ICONS[card_type.lower()]}" alt="{card_type}" class="me-1">'

    allowed_cards_json = json.dumps(list(global_state.config.allowed_credit_cards))

    string_for_cart = f"""
        <script>
            function toggleItemSelection(checkbox) {{
                // Prevent multiple clicks
                if (checkbox.disabled) return;
                const asin = checkbox.dataset.asin;
                const options = checkbox.dataset.options;
                checkbox.disabled = true;
                const originalState = checkbox.checked;
                fetch(`/onlineshop/toggle-selection/${{asin}}`, {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/x-www-form-urlencoded',
                    }},
                    body: new URLSearchParams({{
                        options: options
                    }})
                }})
                .then(response => response.json())
                .then(data => {{
                    if (data.success) {{
                        checkbox.checked = data.selected;
                        const totalElement = document.getElementById('cart-total');
                        if (totalElement) {{
                            totalElement.textContent = data.newTotal;
                        }}
                    }} else {{
                        alert('Failed to update selection: ' + data.error);
                        checkbox.checked = !checkbox.checked;
                    }}
                }})
                .catch(error => {{
                    console.error('Error:', error);
                    checkbox.checked = originalState;
                    alert('Failed to update selection');
                }})
                .finally(() => {{
                    checkbox.disabled = false;
                }})
            }}
        </script>
        <div class="container py-5">
            <h2>Shopping Cart</h2>
            {cart_items_html}
                <div class="card">
                    <div class="card-body">
                        <h3>Selected Items Total: <span id="cart-total">${total:.2f}</span></h3>
                        <form action="/onlineshop/checkout" method="POST">
                            <div class="mb-3">
                                <label for="name">Full Name:</label>
                                <input type="text" class="form-control" id="name" name="name" required>
                            </div>
                            <div class="mb-3">
                                <label for="address">Shipping Address:</label>
                                <textarea class="form-control" id="address" name="address" required></textarea>
                            </div>
                            <div class="mb-3">
                                <label for="card">Card Number:</label>
                                <div class="input-group">
                                    <input type="text" class="form-control" id="card" name="card"
                                        placeholder="xxxx xxxx xxxx xxxx" required>
                                    <span class="input-group-text">
                                        {card_icons_html}
                                    </span>
                                </div>
                            </div>
                            <div class="row">
                                <div class="col-md-6 mb-3">
                                    <label for="expiry">Expiration Date:</label>
                                    <input type="text" class="form-control" id="expiry" name="expiry" 
                                        placeholder="MM/YY" required>
                                </div>
                                <div class="col-md-6 mb-3">
                                    <label for="cvv">CVV:</label>
                                    <input type="text" class="form-control" id="cvv" name="cvv" 
                                        placeholder="123" required>
                                    <small class="text-muted">3 or 4 digits on back of card</small>
                                </div>
                            </div>
                            <button type="submit" class="btn btn-success btn-lg">Place Order</button>
                        </form>
                    </div>
                </div>
            <div class="mt-3">
                <a href="/onlineshop" class="btn btn-secondary">Continue Shopping</a>
            </div>
        </div>
    """
    credit_card_check = """
        <script>
        function getCardType(number) {{
            // Basic regex patterns for card type detection
            const patterns = {{ 
                visa: /^4/,
                mastercard: /^5[1-5]/,
                amex: /^3[47]/,
                discover: /^6(?:011|5)/
            }};
            
            for (const [type, pattern] of Object.entries(patterns)) {{
                if (pattern.test(number)) {{
                    return type;
                }}
            }}
            return null;
        }}

        document.getElementById('card').addEventListener('input', function(e) {{
            const number = e.target.value.replace(/\s+/g, '');
            const cardType = getCardType(number);
            
            // Get allowed cards from Python backend (passed as JSON)
            const allowedCards = {allowed_cards_json}; 
            
            if (number.length > 0 && cardType && !allowedCards.map(c => c.toLowerCase()).includes(cardType)) {{
                e.target.setCustomValidity(`We only accept ${{allowedCards.join(', ')}} cards`);
            }} else {{
                e.target.setCustomValidity('');
            }}
            // Trigger validation feedback
            e.target.reportValidity(); 
        }});
        </script>
    """
    
    if global_state.config.enable_credit_card_check:
        string_for_cart += credit_card_check
    return generate_base_html(string_for_cart)

@router.post("/toggle-selection/{asin}")
async def toggle_selection(asin: str, options: str = Form(...)):
    try:
        selected_options = json.loads(options)
        new_status = global_state.cart.toggle_item_selection(asin, selected_options)
        new_total = global_state.cart.get_selected_total(global_state.product_prices)
        return JSONResponse({
            "success": True,
            "selected": new_status,
            "newTotal": f"${new_total:.2f}"
        })
    except Exception as e:
        return JSONResponse({
            "success": False,
            "error": str(e)
        }, status_code=400)

@router.post("/add-to-cart/{asin}")
async def add_to_cart(asin: str, options: str = Form("{}"), quantity: int = Form(1), image: str = Form(None), request: Request = None):
    selected_options = json.loads(options)
    if not image:
        image = global_state.product_item_dict[asin].get('MainImage', '')
    global_state.cart.add_item(asin, selected_options, quantity, image)
    
    # Get the referer URL or default to the item page
    referer = request.headers.get("referer")
    if not referer:
        referer = f"/onlineshop/item/{asin}"
    
    # Add success parameter to URL
    if "?" in referer:
        redirect_url = f"{referer}&success=true"
    else:
        redirect_url = f"{referer}?success=true"

    # Save cart to a local database
    global_state.save_cart()
    
    return RedirectResponse(url=redirect_url, status_code=303)


@router.post("/update-cart/{asin}")
async def update_cart(asin: str, options: str = Form("{}"), action: str = Form(...)):
    try:
        selected_options = json.loads(options) 
    except json.JSONDecodeError:
         return RedirectResponse(url="/onlineshop/cart?error=invalid_options", status_code=303) # Redirect with error    
    if action == "increase":
        global_state.cart.add_item(asin, selected_options, 1)
    elif action == "decrease":
        global_state.cart.remove_item(asin, selected_options, 1)
        
    return RedirectResponse(url="/onlineshop/cart", status_code=303)

@router.post("/remove-from-cart/{asin}")
async def remove_from_cart(asin: str, options: str = Form("{}")):
    selected_options = json.loads(options)
    global_state.cart.remove_item(asin, selected_options)
    return RedirectResponse(url="/onlineshop/cart", status_code=303)