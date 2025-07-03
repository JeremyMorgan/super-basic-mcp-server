from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
import threading
import uuid
import json
import time

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Global storage for pending requests and client subscriptions
pending_requests = {}
subscriptions = {}
subscription_lock = threading.Lock()

def generate_sse(client_id):
    """Generator function for SSE stream (handles client disconnects)"""
    with subscription_lock:
        queue = subscriptions.get(client_id, [])
    while True:
        if queue:
            event_type, data = queue.pop(0)
            yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
        else:
            time.sleep(0.1)  # Reduce CPU usage

@app.route('/events/<client_id>', methods=['GET'])
def sse_endpoint(client_id):
    """SSE endpoint for a specific client"""
    return Response(
        stream_with_context(generate_sse(client_id)),
        mimetype='text/event-stream'
    )

def notify_client(client_id, event_type, data):
    """Send an event to a specific client's SSE channel"""
    with subscription_lock:
        if client_id in subscriptions:
            subscriptions[client_id].append((event_type, data))

def process_add(request_id, params, client_id):
    """Background task to compute addition and send result via SSE"""
    time.sleep(2)  # Simulate processing delay
    result = params['a'] + params['b']
    # Send result as an SSE event
    notify_client(
        client_id,
        "result",
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": result
        }
    )
    # Clean up pending request
    with subscription_lock:
        if request_id in pending_requests:
            del pending_requests[request_id]

@app.route('/jsonrpc', methods=['POST'])
def jsonrpc_endpoint():
    """Main JSON-RPC endpoint handling MCP requests"""
    data = request.json
    # Validate JSON-RPC envelope
    if not data or 'jsonrpc' not in data or data['jsonrpc'] != '2.0':
        return jsonify({
            "jsonrpc": "2.0",
            "error": {"code": -32600, "message": "Invalid Request"},
            "id": None
        }), 400

    method = data.get('method')
    req_id = data.get('id')
    client_id = request.headers.get('X-Client-ID')

    # Client ID check for methods needing SSE
    if method != 'mcp_discover' and not client_id:
        return jsonify({
            "jsonrpc": "2.0",
            "error": {"code": -32600, "message": "Missing X-Client-ID header"},
            "id": req_id
        }), 400

    # Handle mcp_discover (synchronous)
    if method == 'mcp_discover':
        return jsonify({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "methods": [
                    {
                        "name": "mcp_discover",
                        "description": "List available methods",
                        "parameters": []
                    },
                    {
                        "name": "add",
                        "description": "Add two numbers",
                        "parameters": [
                            {"name": "a", "type": "number"},
                            {"name": "b", "type": "number"}
                        ]
                    }
                ]
            }
        })

    # Handle add (asynchronous with SSE)
    elif method == 'add':
        params = data.get('params', {})
        if 'a' not in params or 'b' not in params:
            return jsonify({
                "jsonrpc": "2.0",
                "error": {"code": -32602, "message": "Invalid parameters"},
                "id": req_id
            }), 400

        # Generate unique request ID for async tracking
        request_id = str(uuid.uuid4())
        pending_requests[request_id] = client_id

        # Start background processing
        thread = threading.Thread(
            target=process_add,
            args=(request_id, params, client_id)
        )
        thread.daemon = True
        thread.start()

        # Immediate acknowledgment response
        return jsonify({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "status": "pending",
                "requestId": request_id
            }
        })

    # Unknown method
    return jsonify({
        "jsonrpc": "2.0",
        "error": {"code": -32601, "message": "Method not found"},
        "id": req_id
    }), 404


def init_subscriptions():
    """Initialize client subscription storage"""
    with subscription_lock:
        subscriptions.clear()

init_subscriptions()  # Call once at startup

if __name__ == '__main__':
    app.run(port=5000, threaded=True)