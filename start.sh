#!/bin/bash
streamlit run motos_prand.py --server.port ${PORT:-8501} --server.address 0.0.0.0 --server.headless true
