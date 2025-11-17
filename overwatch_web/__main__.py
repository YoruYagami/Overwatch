"""Overwatch Web Application Entry Point"""
from overwatch_web.server import create_app

if __name__ == "__main__":
    app = create_app()
    print("\n" + "="*60)
    print("🔍 Overwatch Security Scanner - Web Interface")
    print("="*60)
    print(f"Server running at: http://0.0.0.0:8080")
    print(f"Press CTRL+C to stop the server")
    print("="*60 + "\n")
    app.run(host="0.0.0.0", port=8080, debug=False, threaded=True)
