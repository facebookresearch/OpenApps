"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import random
import json
import logging
from pathlib import Path
from ast import literal_eval
from typing import List, Dict, Optional
from pydantic import BaseModel
import sys
import os
# Add the project root to Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
if project_root not in sys.path:
    sys.path.append(project_root)
from .engine.engine import (
    get_top_n_product_from_keywords,
    get_product_per_page,
)

from .engine.goal import get_reward, get_goals

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from .routes import cart, orders, products
from .models.global_state import global_state
from .templates.html_generator import generate_base_html

app = FastAPI()

# Include routers
app.include_router(products.router, prefix="/onlineshop")
app.include_router(cart.router, prefix="/onlineshop")
app.include_router(orders.router, prefix="/onlineshop")

def update_db_from_hydra(global_state):
    global_state.load_state_from_config()

def set_environment(config):
    global app, global_state
    global_state.initialize()
    app.config = config
    if not os.path.exists(config.onlineshop.database_path):
        os.makedirs(config.onlineshop.database_path)
    global_state.update_config(config.onlineshop)
    update_db_from_hydra(global_state)

def generate_search_results(products: List[Dict], keywords: str, page: int, total: int) -> str:
    products_html = ""
    for product in products:
        products_html += f"""
            <div class="col-lg-12 mb-4">
                <div class="card shadow-sm hover-shadow">
                    <div class="row g-0">
                        <div class="col-md-3">
                            <div class="p-3 d-flex align-items-center justify-content-center" style="height: 100%;">
                                <a href="/onlineshop/item/{product.get('asin', '')}?keywords={keywords}">
                                    <img src="{product.get('MainImage', '')}" 
                                         class="img-fluid rounded" 
                                         alt="{product.get('Title', '')}"
                                         style="max-height: 200px; object-fit: contain;">
                                </a>
                            </div>
                        </div>
                        <div class="col-md-9">
                            <div class="card-body">
                                <h5 class="card-title mb-1">
                                    <a href="/onlineshop/item/{product.get('asin', '')}?keywords={keywords}" 
                                       class="text-decoration-none text-dark">
                                        {product.get('Title', '')}
                                    </a>
                                </h5>
                                <div class="mb-2">
                                    <span class="h4 text-primary">{product.get('Price', '')}</span>
                                    {f'<span class="ms-2"><i class="fa fa-star text-warning"></i> {product.get("average_rating", "")}</span>' 
                                      if product.get('average_rating') else ''}
                                </div>
                                <p class="card-text text-muted">
                                    {product.get('Description', '')[:200]}...
                                </p>
                                <a href="/onlineshop/item/{product.get('asin', '')}?keywords={keywords}" 
                                   class="btn btn-outline-primary">
                                    View Details
                                </a>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        """

    return generate_base_html(f"""
        <div class="container py-5">
            <nav aria-label="breadcrumb" class="mb-4">
                <ol class="breadcrumb">
                    <li class="breadcrumb-item"><a href="/onlineshop">Home</a></li>
                    <li class="breadcrumb-item active">Search Results</li>
                </ol>
            </nav>
            <div class="d-flex justify-content-between align-items-center mb-4">
                <h3>Search Results for "{keywords.replace(',', ' ')}"</h3>
                <span class="text-muted">Page {page} of {(total + 9) // 10} (Total: {total})</span>
            </div>
            <div class="row">
                {products_html}
            </div>
            <nav aria-label="Page navigation" class="mt-4">
                <ul class="pagination justify-content-center">
                    <li class="page-item {'' if page > 1 else 'disabled'}">
                        <a class="page-link" href="/onlineshop/search/{keywords}/{max(1, page-1)}">Previous</a>
                    </li>
                    <li class="page-item {'' if page * 10 < total else 'disabled'}">
                        <a class="page-link" href="/onlineshop/search/{keywords}/{page+1}">Next</a>
                    </li>
                </ul>
            </nav>
        </div>
    """)

@app.post("/onlineshop/search")
async def search(search_query: str = Form(...)):
    keywords = search_query.lower().split()
    return RedirectResponse(url=f"/onlineshop/search/{','.join(keywords)}/1", status_code=303)

@app.get("/onlineshop/search/{keywords}/{page}", response_class=HTMLResponse)
async def search_results(keywords: str, page: int):
    keyword_list = keywords.split(',')
    products = get_top_n_product_from_keywords(
        keyword_list,
        global_state.search_engine,
        global_state.all_products,
        global_state.product_item_dict,
        global_state.attribute_to_asins,
    )
    products_page = get_product_per_page(products, page)
    total = len(products)

    for product in products_page:
        product['search_keywords'] = keywords
    
    return generate_search_results(products_page, keywords, page, total)

# used for reward computation
@app.get("/onlineshop_all")
def get_all(include_product_data: Optional[bool] = False):
    response = {
        "cart": global_state.cart.to_dict(),
        "orders": [order.to_dict() for order in global_state.orders],
    }
    if include_product_data:
        response["product_item_dict"] = global_state.product_item_dict
        response["product_prices"] = global_state.product_prices
    return response

def get_onlineshop_routes():
    return app.routes

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3000)