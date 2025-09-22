"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""
from ..models.global_state import global_state

# HTML Templates
def generate_base_html(content: str) -> str:
    cart_count = global_state.cart.get_total_quantity() if hasattr(global_state, 'cart') else 0
    bg_color = global_state.config.background_color
    font_family = global_state.config.font_family
    base_font_size = global_state.config.base_font_size
    font_color = global_state.config.font_color
    button_background_color = getattr(global_state.config, 'button_background_color', '#0d6efd')
    button_font_color = getattr(global_state.config, 'button_font_color', '#FFFFFF')
    highlight_font_color = getattr(global_state.config, 'highlight_font_color', '#0d6efd')
    return f"""
    <!DOCTYPE html>
    <html>
        <head>
            <title>{global_state.config.title}</title>
            <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
            <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/5.15.4/css/all.min.css" rel="stylesheet">
            <style>
                body {{
                    font-family: "{font_family}";
                    font-size: {base_font_size};
                    background-color: {bg_color};
                    color: {font_color};
                }}
                button, input, textarea, select {{
                    font-family: inherit;
                }}
                h1, h2, h3, h4, h5, h6 {{
                    font-family: inherit;
                }}
                .result-img {{
                    max-width: 100%;
                    height: auto;
                }}
                .item-page-img {{
                    max-width: 100%;
                    height: auto;
                }}
                .top-buffer {{
                    margin-top: 20px;
                }}
                .option-btn {{
                    margin: 2px;
                }}
                .option-btn.active {{
                    background-color: #0d6efd;
                    color: white;
                }}
                .review-item {{
                    border-bottom: 1px solid #ddd;
                    padding: 10px 0;
                }}
                .hover-shadow:hover {{
                    transform: translateY(-3px);
                    box-shadow: 0 4px 20px rgba(0,0,0,0.1) !important;
                    transition: all 0.3s ease;
                }}
                .nav-tabs .nav-link {{
                    color: #666;
                    border: none;
                    padding: 1rem 1.5rem;
                }}
                .nav-tabs .nav-link.active {{
                    color: #0d6efd;
                    border-bottom: 2px solid #0d6efd;
                    background: none;
                }}
                .review-item {{
                    border-bottom: 1px solid #eee;
                    padding: 1rem 0;
                }}
                .review-item:last-child {{
                    border-bottom: none;
                }}
                .btn-primary, .btn-outline-primary {{
                    background-color: {button_background_color} !important;
                    color: {button_font_color} !important;
                }}
                .text-primary {{
                    color: {highlight_font_color} !important;
                }}
                .breadcrumb-item,
                .breadcrumb-item a,
                .breadcrumb-item.active {{
                    color: {highlight_font_color} !important;
                }}
                .breadcrumb-item + .breadcrumb-item::before {{
                    color: {highlight_font_color} !important;
                }}
            </style>
        </head>
        <body>
            <nav class="navbar navbar-expand-lg navbar-light bg-light">
                <div class="container">
                    <a class="navbar-brand" href="/onlineshop">{global_state.config.title}</a>
                    <div class="navbar-nav ms-auto">
                        <a class="nav-link" href="/onlineshop/orders">
                            <i class="fa fa-box"></i> Orders
                        </a>
                        <a class="nav-link" href="/onlineshop/cart">
                            <i class="fa fa-shopping-cart"></i> Cart ({cart_count})
                        </a>
                    </div>
                </div>
            </nav>
            {content}
            <script src="https://ajax.googleapis.com/ajax/libs/jquery/3.5.1/jquery.min.js"></script>
            <script src="https://stackpath.bootstrapcdn.com/bootstrap/4.3.1/js/bootstrap.bundle.min.js"></script>
            <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js"></script>
        </body>
    </html>
    """