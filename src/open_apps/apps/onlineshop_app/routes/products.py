"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from ..templates.html_generator import generate_base_html
from ..models.global_state import global_state
from typing import List, Dict, Optional
import random
import json

router = APIRouter()

def generate_featured_product() -> Dict:
    """Select a random product to feature"""
    all_products = list(global_state.product_item_dict.values())
    return random.choice(all_products)

def generate_homepage() -> str:
    featured_product = generate_featured_product()

    return generate_base_html(f"""
        <div class="container mt-5">
            <!-- Return to Apps Button -->
            <div class="row mb-4">
                <div class="col-12">
                    <a href="/" class="btn btn-primary">
                        <i class="fas fa-arrow-left"></i> Return to List of Apps
                    </a>
                </div>
            </div>

            <!-- Hero Section -->
            <div class="row mb-5">
                <div class="col-md-8 mx-auto text-center">
                    <h1 class="display-4 mb-4">Welcome to {global_state.config.title}</h1>
                    <p class="lead mb-4">{global_state.config.description}</p>
                    <form action="/onlineshop/search" method="POST" class="d-flex justify-content-center">
                        <div class="input-group" style="max-width: 600px;">
                            <input type="text" class="form-control form-control-lg" 
                                   name="search_query" 
                                   placeholder="What are you looking for today?"
                                   aria-label="Search products">
                            <button class="btn btn-primary btn-lg" type="submit">
                                <i class="fa fa-search"></i> Search
                            </button>
                        </div>
                    </form>
                </div>
            </div>

            <!-- Featured Product Section -->
            <div class="row mb-5">
                <div class="col-12 text-center mb-4">
                    <h2 class="display-5 text-primary">{global_state.config.promotional_message}</h2>
                </div>
                <div class="col-md-10 mx-auto">
                    <div class="card shadow-lg">
                        <div class="row g-0">
                            <div class="col-md-6">
                                <div class="p-3 d-flex align-items-center justify-content-center" 
                                     style="height: 100%;">
                                    <img src="{featured_product.get('MainImage', '')}" 
                                         class="img-fluid rounded" 
                                         alt="{featured_product.get('Title', '')}"
                                         style="max-height: 400px; object-fit: contain;">
                                </div>
                            </div>
                            <div class="col-md-6">
                                <div class="card-body p-4">
                                    <h3 class="card-title mb-3">{featured_product.get('Title', '')}</h3>
                                    <div class="mb-3">
                                        <span class="h4 text-primary">{featured_product.get('Price', '')}</span>
                                        <span class="ms-2 badge bg-success">Featured</span>
                                    </div>
                                    <p class="card-text">
                                        {global_state.config.additional_info_to_item + featured_product.get('Description', '')[:200]}...
                                    </p>
                                    <div class="d-grid gap-2">
                                        <a href="/onlineshop/item/{featured_product.get('asin', '')}" 
                                           class="btn btn-primary btn-lg">
                                            View Details
                                        </a>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

        <!-- Search Categories Section -->
        <div class="row">
            <div class="col-12 text-center mb-4">
                <h3>Popular Categories</h3>
            </div>
            <div class="col-md-10 mx-auto">
                <div class="row g-4 justify-content-center">
                    <div class="col-md-4">
                        <div class="card text-center shadow-sm h-100 hover-shadow">
                            <div class="card-body">
                                <i class="fas fa-laptop fa-3x text-info mb-3"></i>
                                <h5 class="card-title">Electronics</h5>
                                <form action="/onlineshop/search" method="POST">
                                    <input type="hidden" name="search_query" value="electronics">
                                    <button type="submit" class="btn btn-outline-info">Browse</button>
                                </form>
                            </div>
                        </div>
                    </div>
                    <div class="col-md-4">
                        <div class="card text-center shadow-sm h-100 hover-shadow">
                            <div class="card-body">
                                <i class="fas fa-tshirt fa-3x text-success mb-3"></i>
                                <h5 class="card-title">Fashion</h5>
                                <form action="/onlineshop/search" method="POST">
                                    <input type="hidden" name="search_query" value="fashion">
                                    <button type="submit" class="btn btn-outline-success">Browse</button>
                                </form>
                            </div>
                        </div>
                    </div>
                    <div class="col-md-4">
                        <div class="card text-center shadow-sm h-100 hover-shadow">
                            <div class="card-body">
                                <i class="fas fa-home fa-3x text-warning mb-3"></i>
                                <h5 class="card-title">Home & Kitchen</h5>
                                <form action="/onlineshop/search" method="POST">
                                    <input type="hidden" name="search_query" value="home kitchen">
                                    <button type="submit" class="btn btn-outline-warning">Browse</button>
                                </form>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    """)

# Update the home route to use the new homepage
@router.get("/", response_class=HTMLResponse)
async def home():
    return generate_homepage()


@router.get("/item/{asin}", response_class=HTMLResponse)
async def item_page(asin: str, sub_page: str = None, request: Request = None):
    product = global_state.product_item_dict.get(asin, {})
    
    # Get query parameters for options
    query_params = dict(request.query_params) if request else {}
    success_message = """
        <div class="alert alert-success alert-dismissible fade show" role="alert">
            Item added to cart successfully!
            <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
        </div>
        <script>
            // Remove success message after 3 seconds
            setTimeout(function() {
                const successAlert = document.getElementById('success-alert');
                if (successAlert) {
                    successAlert.style.transition = 'opacity 0.5s';
                    successAlert.style.opacity = '0';
                    setTimeout(() => successAlert.remove(), 500);
                }
                // Clean up the URL
                const url = new URL(window.location);
                url.searchParams.delete('success');
                window.history.replaceState({}, '', url);
            }, 3000);
        </script>
    """ if query_params.get('success') == 'true' else ""
    keywords = query_params.get('keywords', '')
    initial_options = {}
    
    # Extract options from query parameters
    if 'options' in product:
        for option_name in product['options'].keys():
            param_key = f"option_{option_name}"
            if param_key in query_params:
                initial_options[option_name] = query_params[param_key]
            else:
                # Set default to first value if not in query params
                initial_options[option_name] = product['options'][option_name][0] if product['options'][option_name] else ''

    # Generate options HTML with initial values and price data
    options_html = ""
    if 'options' in product:
        for option_name, option_values in product['options'].items():
            current_value = initial_options.get(option_name, option_values[0] if option_values else '')
            options_html += f"""
                <div class='mt-3'>
                    <h4>{option_name}</h4>
                    <div class='d-flex flex-wrap gap-2'>
                        {''.join([f'''
                            <button type='button' 
                                class='btn btn-outline-secondary option-btn btn-sm {("active" if value == current_value else "")}' 
                                data-option-name='{option_name}'
                                data-option-value='{value}'
                                data-price='{global_state.product_prices[asin]}'
                                data-image='{product.get("option_to_image", {}).get(value, "")}'
                                style='min-width: fit-content; font-size: 0.875rem; padding: 0.25rem 0.5rem;'>{value}</button>
                        ''' for value in option_values])}
                    </div>
                </div>
            """

    # Generate sub page content
    sub_page_content = ""
    if sub_page == "Description":
        sub_page_content = f"<div class='mt-3'><h4>Description:</h4><p>{product.get('Description', 'No description available.')}</p></div>"
    elif sub_page == "Features":
        features = product.get('BulletPoints', [])
        features_html = "".join([f"<li>{feature}</li>" for feature in features])
        sub_page_content = f"<div class='mt-3'><h4>Features:</h4><ul>{features_html}</ul></div>"
    elif sub_page == "Reviews":
        reviews = product.get('Reviews', [])
        reviews_html = "".join([f"<div class='review-item'><p>{review}</p></div>" for review in reviews])
        sub_page_content = f"<div class='mt-3'><h4>Reviews:</h4>{reviews_html}</div>"

    return generate_base_html(f"""
        <div class="container py-5">
            {success_message}
            <nav aria-label="breadcrumb" class="mb-4">
                <ol class="breadcrumb">
                    <li class="breadcrumb-item"><a href="/onlineshop">Home</a></li>
                    <li class="breadcrumb-item"><a href="/onlineshop/search/{keywords}/1">Search Results</a></li>
                    <li class="breadcrumb-item active">Product Details</li>
                </ol>
            </nav>
            <div class="row">
                <div class="col-md-5 mb-4">
                    <div class="card shadow-sm">
                        <div class="card-body">
                            <div class="text-center mb-4">
                                <img id="product-image" src="{product.get('MainImage', '')}" 
                                     class="img-fluid rounded" 
                                     style="max-height: 400px; object-fit: contain;">
                            </div>
                            {options_html}
                        </div>
                    </div>
                </div>
                <div class="col-md-5">
                    <div class="card shadow-sm h-100">
                        <div class="card-body">
                            <h2 class="card-title mb-3">{product.get('Title', 'Product Not Found')}</h2>
                            <div class="d-flex align-items-center mb-3">
                                <h3 class="text-primary mb-0">${global_state.product_prices[asin]:.2f}</h3>
                                {f'<div class="ms-3"><i class="fa fa-star text-warning"></i> {product.get("Rating", "N/A")}</div>' 
                                  if product.get('Rating') else ''}
                            </div>
                            
                            <ul class="nav nav-tabs mb-3">
                                <li class="nav-item">
                                    <button class="nav-link {'' if sub_page != 'Description' else 'active'}" 
                                            onclick="navigateToSubPage('Description', '{asin}')">Description</button>
                                </li>
                                <li class="nav-item">
                                    <button class="nav-link {'' if sub_page != 'Features' else 'active'}" 
                                            onclick="navigateToSubPage('Features', '{asin}')">Features</button>
                                </li>
                                <li class="nav-item">
                                    <button class="nav-link {'' if sub_page != 'Reviews' else 'active'}" 
                                            onclick="navigateToSubPage('Reviews', '{asin}')">Reviews</button>
                                </li>
                            </ul>
                            <div class="tab-content">
                                {sub_page_content}
                            </div>
                        </div>
                    </div>
                </div>
                <div class="col-md-2">
                    <div class="card shadow-sm">
                        <div class="card-body">
                            <form action="/onlineshop/add-to-cart/{asin}" method="POST">
                                <input type="hidden" id="selected-options" name="options" value="{{}}" />
                                <input type="hidden" id="current-image" name="image" value="{product.get('MainImage', '')}" />
                                <div class="mb-3">
                                    <label for="quantity" class="form-label">Quantity:</label>
                                    <input type="number" id="quantity" name="quantity" value="1" min="1" class="form-control">
                                </div>
                                <button type="submit" class="btn btn-primary btn-lg w-100 mb-3">Add to Cart</button>
                            </form>
                            <a href="/onlineshop/search/{keywords}/1" class="btn btn-outline-secondary w-100">Back to Search</a>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        <script>
        // Store selected options and current image state 
        let selectedOptions = {json.dumps(initial_options)};
        let mainImage = "{product.get('MainImage', '')}";
        let currentImage = mainImage;

        // Set initial selected options and check for initial image from active options
        document.addEventListener('DOMContentLoaded', function() {{
            // Get URL parameters to ensure correct state on back navigation
            const urlParams = new URLSearchParams(window.location.search);
            const optionParams = {{}};
            
            // Extract all option parameters from URL
            for (const [key, value] of urlParams.entries()) {{
                if (key.startsWith('option_')) {{
                    const optionName = key.replace('option_', '');
                    optionParams[optionName] = value;
                }}
            }}
            
            // Update buttons and images based on URL parameters
            let activeOptionImage = null;
            document.querySelectorAll('.option-btn').forEach(button => {{
                const optionName = button.dataset.optionName;
                const optionValue = button.dataset.optionValue;

                // First clear any existing active states
                button.classList.remove('active');
                
                // Check if this option matches URL parameters
        if ((optionParams[optionName] && optionParams[optionName] === optionValue) ||
            (!optionParams[optionName] && selectedOptions[optionName] === optionValue)) {{
                    button.classList.add('active');
                    selectedOptions[optionName] = optionValue;
                    
                    // Update image if this option has one
                    const image = button.dataset.image;
                    if (image && image !== 'None' && image !== 'undefined' && image.trim() !== '') {{
                        activeOptionImage = image;
                    }}
                }}
            }});

            currentImage = activeOptionImage || mainImage;
            document.getElementById('product-image').src = currentImage;
            document.getElementById('current-image').value = currentImage;

            // Update hidden form field with initial options
            document.getElementById('selected-options').value = JSON.stringify(selectedOptions);
        }});

        // Handle option button clicks
        document.querySelectorAll('.option-btn').forEach(button => {{
            button.addEventListener('click', function(e) {{
                e.preventDefault();
                e.stopPropagation();
                
                const optionName = this.dataset.optionName;
                const optionValue = this.dataset.optionValue;
                const image = this.dataset.image;
                const currentPath = window.location.pathname;
                const asin = currentPath.split('/').pop();


                // Update selected options
                selectedOptions[optionName] = optionValue;
                document.getElementById('selected-options').value = JSON.stringify(selectedOptions);


                // Update active state for this option group
                document.querySelectorAll(`[data-option-name="${{optionName}}"]`).forEach(btn => {{
                    btn.classList.remove('active');
                }});
                this.classList.add('active');


                // Update image if available from option, otherwise revert to main image
                if (image && image !== 'None' && image !== 'undefined' && image.trim() !== '') {{
                    currentImage = image;
                }}

                document.getElementById('product-image').src = currentImage;
                document.getElementById('current-image').value = currentImage;
        

                // Construct new URL with all current options
                const params = new URLSearchParams(window.location.search);  // Get existing params
                Object.entries(selectedOptions).forEach(([key, value]) => {{
                    params.set(`option_${{key}}`, value);
                }});
                
                // Preserve sub_page parameter if it exists
                const currentParams = new URLSearchParams(window.location.search);
                const subPage = currentParams.get('sub_page');
                if (subPage) {{
                    params.set('sub_page', subPage);
                }}


                // Navigate to updated URL
                window.location.href = `/onlineshop/item/${{asin}}?${{params.toString()}}`;
            }});
        }});

        // Handle sub-page navigation while preserving options
        function navigateToSubPage(subPage, asin) {{
            const params = new URLSearchParams();
            params.append('sub_page', subPage);
            
            // Add current options to URL
            Object.entries(selectedOptions).forEach(([key, value]) => {{
                params.append(`option_${{key}}`, value);
            }});

            window.location.href = `/onlineshop/item/${{asin}}?${{params.toString()}}`;
        }}
        </script>
    """)