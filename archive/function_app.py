import azure.functions as func
import logging
import base64
import os
import requests
import json
import uuid
import time
from PIL import Image

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

# Set upload directory based on environment
# Use /tmp in Azure Functions cloud environment, otherwise use current directory
if os.environ.get('FUNCTIONS_ENV') == "local":  # This environment variable exists in Azure Functions
    UPLOAD_DIR = os.path.dirname(os.path.realpath(__file__))  # Use current directory
else:
    UPLOAD_DIR = "/tmp"

FUNCTIONS_KEY = os.environ.get('FUNCTIONS_KEY')
ANALYZER_ENDPOINT = os.environ.get('ANALYZER_ENDPOINT')
ANALYZER_API_KEY = os.environ.get('ANALYZER_API_KEY')
ANALYZER_API_VERSION = os.environ.get('ANALYZER_API_VERSION')
TIMEOUT_SECONDS = os.environ.get('TIMEOUT_SECONDS')
POLLING_SECONDS = os.environ.get('POLLING_SECONDS')


# Function to validate API key from headers
def validate_api_key(req: func.HttpRequest) -> bool:
    # Get the API key from environment variables
    if not FUNCTIONS_KEY:
        logging.warning("validate_api_key - FUNCTIONS_KEY environment variable is not set")
        return False
        
    # Get the API key from the request header
    request_api_key = req.headers.get('x-functions-key')
    if not request_api_key:
        logging.warning("validate_api_key - API key not provided in request headers")
        return False
        
    # Compare the keys
    return request_api_key == FUNCTIONS_KEY


def convert_webp_to_jpeg(webp_file_path, output_file_base):
    """
    Convert a WebP image to JPEG format
    
    Args:
        webp_file_path: Path to the WebP file
        output_file_base: Base output file path without extension
    
    Returns:
        Path to the JPEG file or original WebP file if conversion fails
    """
    try:
        # Convert the WebP to JPEG
        with Image.open(webp_file_path) as img:
            rgb_img = img.convert('RGB')  # Convert to RGB to ensure compatibility with JPEG
            jpeg_file = output_file_base + ".jpg"
            rgb_img.save(jpeg_file, 'JPEG', quality=90)
        
        # Remove the temporary WebP file
        if os.path.exists(webp_file_path):
            os.remove(webp_file_path)
            
        return jpeg_file
    except Exception as e:
        logging.error(f"Error converting WebP to JPEG: {str(e)}")
        # If conversion fails, return the original WebP file
        return webp_file_path


def base64_to_file(base64_string, output_file):
    # Decode the base64 string
    file_data = base64.b64decode(base64_string)
    
    # Determine the file type and extension
    if base64_string.startswith("JVBERi0"):
        file_extension = ".pdf"
    elif base64_string.startswith("/9j/"):
        file_extension = ".jpg"
    elif base64_string.startswith("iVBORw0"):
        file_extension = ".png"
    elif base64_string.startswith("UklGR"):
        # WebP file detected
        file_extension = ".webp"
        temp_webp_file = output_file + file_extension
        
        # Write the decoded data to a temporary WebP file
        with open(temp_webp_file, "wb") as file:
            file.write(file_data)
        
        # Convert WebP to JPEG using the separate function
        return convert_webp_to_jpeg(temp_webp_file, output_file)
    elif base64_string.startswith("Qk"):
        file_extension = ".bmp"
    else:
        raise ValueError("Unsupported file type")
    
    # Create the output file with the appropriate extension
    output_file_with_extension = output_file + file_extension
    
    # Write the decoded data to the file
    with open(output_file_with_extension, "wb") as file:
        file.write(file_data)
    
    return output_file_with_extension

# Function to get analysis result from the analyzer API
def get_analysis(request_id, headers: dict, analyzer_id: str) -> func.HttpResponse:
    """
    Function to get the analysis result from the analyzer API.
    It polls the API until the analysis is complete or times out.
    """
    log_data = {}
    result_data = {
        "id": request_id
    }
    
    result_url = f"{ANALYZER_ENDPOINT}/contentunderstanding/analyzers/{analyzer_id}/results/{request_id}?api-version={ANALYZER_API_VERSION}"
    log_data['result_url'] = result_url
    log_data['headers'] = headers

    try:
        start_time = time.time()
        while True:
            elapsed_time = time.time() - start_time
            logging.info(f"get_analysis - Elapsed time: {elapsed_time} seconds")

            if elapsed_time > int(TIMEOUT_SECONDS):
                log_data['elapsed_time'] = elapsed_time
                raise TimeoutError(f"Request timed out after {TIMEOUT_SECONDS} seconds")

            # Get the result of the analysis
            result_response = requests.get(result_url, headers=headers)
            result_json = result_response.json()

            status = result_json.get('status')
            log_data['status'] = status

            if status == 'Succeeded':
                # Check if we have contents data
                if 'result' in result_json and 'contents' in result_json['result'] and result_json['result']['contents']:
                    # Get the first content item
                    content = result_json['result']['contents'][0]
                    
                    # Add markdown if available
                    if 'markdown' in content:
                        result_data['markdown'] = content['markdown']
                    
                    # Add fields if available
                    if 'fields' in content:
                        fields = content['fields']
                        # Extract key-value pairs from fields
                        for field_name, field_data in fields.items():
                            if 'valueString' in field_data:
                                result_data[field_name] = field_data['valueString']

                    return func.HttpResponse(
                        body=json.dumps(result_data),
                        mimetype="application/json",
                        status_code=200
                    )
            
            time.sleep(int(POLLING_SECONDS))

    except Exception as e:
        log_data['error'] = str(e)
        return func.HttpResponse(
            body=json.dumps(log_data),
            mimetype="application/json",
            status_code=500
        )


def get_file(filename: str) -> func.HttpResponse:
    logging.info('get_file - request received')

    # Validate filename to prevent directory traversal attacks
    if ".." in filename or "/" in filename or "\\" in filename:
        return func.HttpResponse("Invalid filename", status_code=400)
    
    # Construct the file path
    file_path = os.path.join(UPLOAD_DIR, filename)
    
    # Check if the file exists
    if not os.path.exists(file_path):
        return func.HttpResponse("File not found", status_code=404)
    
    # Determine content type based on file extension
    content_type = "application/octet-stream"  # Default content type
    if filename.endswith(".pdf"):
        content_type = "application/pdf"
    elif filename.endswith(".jpg") or filename.endswith(".jpeg"):
        content_type = "image/jpeg"
    elif filename.endswith(".png"):
        content_type = "image/png"
    elif filename.endswith(".bmp"):
        content_type = "image/bmp"
    
    # Read the file content
    with open(file_path, "rb") as file:
        file_content = file.read()
    
    # Return the file content with the appropriate content type
    return func.HttpResponse(
        body=file_content,
        mimetype=content_type,
        status_code=200
    )


@app.route(route="check_docs")
def check_docs(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("check_docs - request received")
    
    # Get the filename from the query parameter
    filename = req.params.get('filename')
    if filename:
        return get_file(filename)

    # Validate API key
    if not validate_api_key(req):
        return func.HttpResponse("Unauthorized. Invalid or missing API key.", status_code=401)

    result_file = None
    log_data = {}

    try:
        req_body = req.get_json()
        
        # Validate request body
        if not req_body:
            return func.HttpResponse("Invalid request. Request body is required.", status_code=400)
            
        # Validate required fields
        base64_string = req_body.get('content')
        analyzer_id = req_body.get('analyzerId')
        
        if not base64_string:
            return func.HttpResponse("Invalid request. 'content' field is required.", status_code=400)
            
        if not analyzer_id:
            return func.HttpResponse("Invalid request. 'analyzerId' field is required.", status_code=400)
            
        # Validate base64 format
        try:
            # Try to decode a small part to check if it's valid base64
            base64.b64decode(base64_string[:20])
        except Exception:
            return func.HttpResponse("Invalid request. 'content' must be a valid base64 string.", status_code=400)
        
        # Generate a unique filename with uuid
        unique_id = str(uuid.uuid4())
        temp_file = os.path.join(UPLOAD_DIR, unique_id)
        
        result_file = base64_to_file(base64_string, temp_file)
        file_name = os.path.basename(result_file)
        log_data['result_file'] = result_file
        
        # Get the host URL from the request
        host = req.headers.get('host', 'localhost:7071')
        scheme = req.headers.get('x-forwarded-proto', 'https')
        
        # Create the file URL using our own endpoint
        fileUrl = f"{scheme}://{host}/api/check_docs?filename={file_name}"
        log_data['fileUrl'] = fileUrl
        
        # Make the API call to the content understanding analyzer
        api_url = f"{ANALYZER_ENDPOINT}/contentunderstanding/analyzers/{analyzer_id}:analyze?api-version={ANALYZER_API_VERSION}"
        log_data['api_url'] = api_url
        
        # Validate analyzer environment variables
        if not ANALYZER_ENDPOINT or not ANALYZER_API_KEY or not ANALYZER_API_VERSION:
            return func.HttpResponse("Server configuration error. Missing analyzer environment variables.", status_code=500)
            
        headers = {
            "Ocp-Apim-Subscription-Key": ANALYZER_API_KEY,
            "Content-Type": "application/json"
        }

        # https://imgv2-2-f.scribdassets.com/img/document/742005325/original/00d9264bc2/1?v=1
        payload = {
            "url": fileUrl
        }
        
        # Send the request to the analyzer API
        response = requests.post(api_url, headers=headers, data=json.dumps(payload))

        # Check if the response is successful
        if str(response.status_code) not in ['202', '200']:
            raise Exception(f"Analyzer API request failed with status code {response.status_code}")

        response_json = response.json()
        request_id = response_json.get('id')

        # Check if the request ID is present in the response
        if not request_id:
            raise Exception(f"Analyzer API response does not contain a valid request ID: {response_json}")
        
        # Delete the temporary file after getting the API response
        if result_file and os.path.exists(result_file):
            os.remove(result_file)
            log_data['temp_file_deleted'] = True

        return get_analysis(request_id, headers, analyzer_id)

    except Exception as e:
        log_data['error'] = str(e)
        return func.HttpResponse(
            body=json.dumps(log_data),
            mimetype="application/json",
            status_code=500
        )
    finally:
        # Ensure file cleanup happens even if an exception occurs
        if result_file and os.path.exists(result_file):
            try:
                # os.remove(result_file)
                logging.info(f"check_docs - Temporary file deleted in finally block: {result_file}")
            except Exception as e:
                logging.error(f"check_docs - Error deleting temporary file: {str(e)}")
