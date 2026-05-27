#!/bin/bash
set -e

echo "=== ImpuestIA RAG Bot startup ==="
echo "RAG: Supabase pgvector"
echo "LLM: GPT-4o"

exec python main.py
