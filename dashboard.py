import os
from flask import Flask, render_template, request
from bigquery_helper import get_documents

# Use the directory containing this script as the template folder.
# This allows dashboard.html to be found in the same folder as dashboard.py.
app = Flask(__name__, template_folder=os.path.dirname(os.path.abspath(__file__)))

@app.route('/')
def index():
    tag_query = request.args.get('tag', '')
    documents = get_documents(tag_filter=tag_query)
    return render_template('dashboard.html', documents=documents, tag_query=tag_query)

if __name__ == '__main__':
    # For local development
    port = int(os.environ.get('PORT', 8081))
    app.run(host='0.0.0.0', port=port)