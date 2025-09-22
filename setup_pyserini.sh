#!/bin/bash

# Build search engine index
cd src/open_apps/apps/onlineshop_app/search_engine
mkdir -p resources resources_100 resources_1k # resources_100k
python convert_product_file_format.py # convert items.json => required doc format
mkdir -p indexes
chmod +x run_indexing.sh
./run_indexing.sh
cd ..

# return to the original directory
cd ../../../../