#!/usr/bin/env python3
"""Overwatch Security Scanner - Main Entry Point"""
import sys
import os

# Add the project directory to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from overwatch_web.server import create_app

if __name__ == "__main__":
    app = create_app()

    print("\n" + "=" * 70)
    print("🔍 Overwatch Security Scanner - Web Interface")
    print("=" * 70)
    print(f"Server running at: http://0.0.0.0:8080")
    print(f"Press CTRL+C to stop the server")
    print("=" * 70 + "\n")

    try:
        app.run(host="0.0.0.0", port=8080, debug=False, threaded=True)
    except KeyboardInterrupt:
        print("\n\nShutting down gracefully...")
        sys.exit(0)
