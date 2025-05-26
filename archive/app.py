import logging
import base64
import os
import requests
import json
import uuid
import time
from PIL import Image
from fastapi import FastAPI, HTTPException, Request, Response, Depends, Header
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import io
from typing import Optional, Dict, Any
from pydantic import BaseModel

# Load environment variables from .env file if it exists
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# Create FastAPI app
app = FastAPI(
    title="XCash API",
    description="FastAPI application for document analysis",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Set upload directory based on environment
UPLOAD_DIR = os.path.dirname(os.path.realpath(__file__))

# Load configuration from environment variables
API_KEY = os.environ.get('FUNCTIONS_KEY')
ANALYZER_ENDPOINT = os.environ.get('ANALYZER_ENDPOINT')
ANALYZER_API_KEY = os.environ.get('ANALYZER_API_KEY')
ANALYZER_API_VERSION = os.environ.get('ANALYZER_API_VERSION')
TIMEOUT_SECONDS = int(os.environ.get('TIMEOUT_SECONDS', 120))
POLLING_SECONDS = int(os.environ.get('POLLING_SECONDS', 1))

# Define request model
class DocumentRequest(BaseModel):
    content: str  # Base64 encoded file content
    analyzerId: str

# Function to validate API key from headers
async def validate_api_key(x_api_key: str = Header(..., description="API key for authentication", alias="X-API-Key")):
    # Get the API key from environment variables
    if not API_KEY:
        logging.warning("validate_api_key - API_KEY environment variable is not set")
        raise HTTPException(status_code=401, detail="API key configuration missing")
        
    # Compare the keys
    if not x_api_key or x_api_key != API_KEY:
        logging.warning(f"validate_api_key - API key not provided or invalid: {x_api_key}")
        raise HTTPException(status_code=401, detail="Unauthorized. Invalid or missing API key.")
    
    return True

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
    """
    Convert base64 string to a file and save it to disk
    
    Args:
        base64_string: Base64 encoded string representing file content
        output_file: Base output file path without extension
    
    Returns:
        Path to the saved file
    """
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

async def get_analysis(request_id: str, headers: dict, analyzer_id: str):
    """
    Function to get the analysis result from the analyzer API.
    It polls the API until the analysis is complete or times out.
    
    Args:
        request_id: Request ID from the analyzer API
        headers: HTTP headers for the API request
        analyzer_id: ID of the analyzer to use
    
    Returns:
        JSON response with analysis results or error information
    """
    log_data = {}
    result_data = {
        "id": request_id
    }
    
    result_url = f"{ANALYZER_ENDPOINT}/contentunderstanding/analyzers/{analyzer_id}/results/{request_id}?api-version={ANALYZER_API_VERSION}"
    log_data['result_url'] = result_url
    
    # Remove API key from logged headers
    safe_headers = headers.copy()
    if "Ocp-Apim-Subscription-Key" in safe_headers:
        safe_headers["Ocp-Apim-Subscription-Key"] = "***"
    log_data['headers'] = safe_headers

    try:
        start_time = time.time()
        while True:
            elapsed_time = time.time() - start_time
            logging.info(f"get_analysis - Elapsed time: {elapsed_time} seconds")

            if elapsed_time > TIMEOUT_SECONDS:
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

                    return JSONResponse(
                        content=result_data,
                        status_code=200
                    )
            
            time.sleep(POLLING_SECONDS)

    except Exception as e:
        log_data['error'] = str(e)
        return JSONResponse(
            content=log_data,
            status_code=500
        )

@app.get("/")
async def root():
    """Root endpoint that returns a welcome message"""
    return {"message": "Welcome to XCash API", "status": "operational"}

@app.get("/api/check_docs")
async def get_file_endpoint(filename: str):
    """
    Endpoint to retrieve a file by its filename
    
    Args:
        filename: Name of the file to retrieve
    
    Returns:
        File content with appropriate content type
    """
    logging.info('get_file - request received for file: %s', filename)

    # Validate filename to prevent directory traversal attacks
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    
    # Construct the file path
    file_path = os.path.join(UPLOAD_DIR, filename)
    
    # Check if the file exists
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    
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
    return StreamingResponse(io.BytesIO(file_content), media_type=content_type)

@app.post("/api/check_docs")
async def check_docs(
    request: Request,
    doc_request: DocumentRequest,
    api_key_valid: bool = Depends(validate_api_key)
):
    """
    Main endpoint to process documents and send them to the analyzer API
    
    Args:
        request: FastAPI request object
        doc_request: Document request containing base64 content and analyzer ID
        api_key_valid: Dependency to validate API key
    
    Returns:
        Analysis results from the analyzer API
    """
    logging.info("check_docs - request received")
    
    result_file = None
    log_data = {}

    try:
        # Get the required fields
        base64_string = doc_request.content
        analyzer_id = doc_request.analyzerId
        
        # Validate base64 format
        try:
            # Try to decode a small part to check if it's valid base64
            base64.b64decode(base64_string[:20])
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid request. 'content' must be a valid base64 string.")
        
        # Generate a unique filename with uuid
        unique_id = str(uuid.uuid4())
        temp_file = os.path.join(UPLOAD_DIR, unique_id)
        
        result_file = base64_to_file(base64_string, temp_file)
        file_name = os.path.basename(result_file)
        log_data['result_file'] = result_file
        
        # Get the host URL from the request
        host = request.headers.get('host', 'localhost:8000')
        scheme = request.headers.get('x-forwarded-proto', 'http')
        
        # Create the file URL using our own endpoint
        fileUrl = f"{scheme}://{host}/api/check_docs?filename={file_name}"
        log_data['fileUrl'] = fileUrl
        
        # Make the API call to the content understanding analyzer
        api_url = f"{ANALYZER_ENDPOINT}/contentunderstanding/analyzers/{analyzer_id}:analyze?api-version={ANALYZER_API_VERSION}"
        log_data['api_url'] = api_url
        
        # Validate analyzer environment variables
        if not ANALYZER_ENDPOINT or not ANALYZER_API_KEY or not ANALYZER_API_VERSION:
            raise HTTPException(status_code=500, detail="Server configuration error. Missing analyzer environment variables.")
            
        headers = {
            "Ocp-Apim-Subscription-Key": ANALYZER_API_KEY,
            "Content-Type": "application/json"
        }

        payload = {
            "url": fileUrl
        }
        
        # Send the request to the analyzer API
        response = requests.post(api_url, headers=headers, data=json.dumps(payload))

        # Check if the response is successful
        if str(response.status_code) not in ['202', '200']:
            raise HTTPException(
                status_code=500, 
                detail=f"Analyzer API request failed with status code {response.status_code}"
            )

        response_json = response.json()
        request_id = response_json.get('id')

        # Check if the request ID is present in the response
        if not request_id:
            raise HTTPException(
                status_code=500, 
                detail=f"Analyzer API response does not contain a valid request ID: {response_json}"
            )
        
        # We'll keep the file for the analyzer to access
        # It will be cleaned up in the finally block

        return await get_analysis(request_id, headers, analyzer_id)

    # except HTTPException:
    #     raise  # Re-raise HTTP exceptions
    except Exception as e:
        log_data['error'] = str(e)
        return JSONResponse(
            content=log_data,
            status_code=500
        )
    finally:
        # Ensure file cleanup happens even if an exception occurs
        if result_file and os.path.exists(result_file):
            try:
                os.remove(result_file)
                logging.info(f"check_docs - Temporary file deleted in finally block: {result_file}")
            except Exception as e:
                logging.error(f"check_docs - Error deleting temporary file: {str(e)}")

# For local development
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
