import os
os.chdir(r"D:\document\寝室电费")
import http.server
import socketserver
Handler = http.server.SimpleHTTPRequestHandler
with socketserver.TCPServer(("0.0.0.0", 8080), Handler) as httpd:
    print("Serving on port 8080...")
    httpd.serve_forever()
