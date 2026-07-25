# Entry point compatibility wrapper for Render (gunicorn app:app & gunicorn server:app)
from server import app

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=True)
