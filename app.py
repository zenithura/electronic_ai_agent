import os
import pathlib
from flask import Flask, render_template, request, jsonify, session, send_from_directory, abort
from werkzeug.utils import secure_filename
import time
import uuid
import io
import tempfile
import base64
from PIL import Image
from dotenv import load_dotenv
import json
import traceback
from supabase import create_client, Client
from flask_cors import CORS  # CORS için

# PDF işleme için
from PyPDF2 import PdfReader
import fitz  # PyMuPDF - PDF görüntüleme ve resim çıkarma için

# Google Gemini AI için
import google.generativeai as genai
from google.generativeai.types import content_types, generation_types

# .env dosyasını yükle
load_dotenv()

# Supabase istemcisini başlat
supabase_url = os.environ.get("SUPABASE_URL")
supabase_key = os.environ.get("SUPABASE_SERVICE_KEY", os.environ.get("SUPABASE_KEY"))
supabase: Client = create_client(supabase_url, supabase_key)

# Konfigürasyon
ALLOWED_EXTENSIONS = {'pdf'}
ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
UPLOAD_FOLDER = 'uploads'  # Geçici yükleme işlemleri için

app = Flask(__name__)
CORS(app)  # CORS desteği ekle
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'gizli-anahtar-burada')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 64 * 1024 * 1024  # 64 MB max upload olarak artırıldı
app.config['SESSION_TYPE'] = 'filesystem'
app.config['PERMANENT_SESSION_LIFETIME'] = 3600  # Session ömrü (saniye)
app.config['JSON_AS_ASCII'] = False  # UTF-8 karakter desteği

class InteractivePDFAssistant:
    """
    An assistant class that enables interactive work with PDF documents, with the ability
    to ask questions and generate content.
    """
    
    def __init__(self, api_key: str):
        """
        Initializes the InteractivePDFAssistant class.
        
        Args:
            api_key: Gemini AI API key
        """
        # Configure API
        genai.configure(api_key=api_key)
        
        # Model selection - use the latest and fastest model
        self.model_name = "gemini-1.5-flash"
        self.model = genai.GenerativeModel(
            self.model_name,
            generation_config={
                "max_output_tokens": 8192,
                "temperature": 0.4,
            },
            safety_settings=[
                {
                    "category": "HARM_CATEGORY_HARASSMENT",
                    "threshold": "BLOCK_NONE"
                },
                {
                    "category": "HARM_CATEGORY_HATE_SPEECH",
                    "threshold": "BLOCK_NONE"
                },
                {
                    "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    "threshold": "BLOCK_NONE"
                },
                {
                    "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                    "threshold": "BLOCK_NONE"
                }
            ]
        )
        
        # Current PDF and content
        self.current_pdf_id = None
        self.current_pdf_filename = None
        self.pdf_text = ""
        self.pdf_raw_bytes = None
        self.pdf_title = ""
        
        # Chat history and context
        self.chat_session = None
        self.chat_history = []
        
        print("PDF Assistant initialized.")
    
    def create_chat_session(self):
        """Starts a new chat session"""
        self.chat_session = self.model.start_chat(
            history=self.chat_history
        )
        print("New chat session started.")
    
    def _truncate_pdf_for_api(self, pdf_bytes, max_size_mb=10):
        """Truncates PDF to appropriate size for API"""
        max_size_bytes = max_size_mb * 1024 * 1024  # MB to bytes
        
        if len(pdf_bytes) <= max_size_bytes:
            return pdf_bytes
        
        print(f"PDF size ({len(pdf_bytes)/1024/1024:.2f} MB) is too large, truncating...")
        
        try:
            # Write PDF to temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as temp_file:
                temp_file.write(pdf_bytes)
                temp_path = temp_file.name
            
            # Open PDF with PyMuPDF
            doc = fitz.open(temp_path)
            
            # Create a new PDF with first pages
            new_doc = fitz.open()
            
            # Add pages one by one checking the size
            for i in range(min(10, len(doc))):  # Maximum 10 pages
                new_doc.insert_pdf(doc, from_page=i, to_page=i)
                
                # Check the size of the new PDF
                with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as check_file:
                    new_doc.save(check_file.name)
                    check_file.flush()
                    current_size = os.path.getsize(check_file.name)
                
                # If size limit exceeded, remove the last page and exit loop
                if current_size > max_size_bytes:
                    if i > 0:  # At least keep one page
                        new_doc.delete_page(i)
                    break
            
            # Save the new PDF as byte array
            output_stream = io.BytesIO()
            new_doc.save(output_stream)
            truncated_pdf = output_stream.getvalue()
            
            # Close files and clean up
            new_doc.close()
            doc.close()
            os.unlink(temp_path)
            
            print(f"PDF truncated to {len(truncated_pdf)/1024/1024:.2f} MB.")
            return truncated_pdf
            
        except Exception as e:
            print(f"PDF truncation error: {str(e)}")
            # Return original PDF in case of error, API may still reject it
            return pdf_bytes
    
    def load_pdf_from_supabase(self, pdf_id: str, filename: str, bucket_name: str = "pdfs") -> bool:
        """
        Loads a PDF file from Supabase and extracts its content.
        
        Args:
            pdf_id: ID of the PDF file
            filename: Name of the PDF file
            bucket_name: Supabase bucket name
            
        Returns:
            True if loading successful, False otherwise
        """
        try:
            print(f"PDF loading: {filename} (ID: {pdf_id})")
            
            # PDF information to save
            self.current_pdf_id = pdf_id
            self.current_pdf_filename = filename
            self.pdf_title = filename
            
            # Extract filename (full path may be provided)
            if "/" in filename:
                filename = filename.split("/")[-1]
                print(f"Filename extracted: {filename}")
            
            # Extract timestamp from filename if exists (e.g., 1709123456_file.pdf -> file.pdf)
            if '_' in filename and filename.split('_')[0].isdigit():
                parts = filename.split('_', 1)
                if len(parts) > 1:
                    clean_filename = parts[1]
                else:
                    clean_filename = filename
            else:
                clean_filename = filename
                
            print(f"Cleaned filename: {clean_filename}")
            
            # List all files in the bucket
            try:
                print(f"Listing files from '{bucket_name}' bucket...")
                storage_files = supabase.storage.from_(bucket_name).list()
                print(f"Bucket '{bucket_name}' has {len(storage_files)} files.")
                
                # List file names and paths
                storage_file_names = [f["name"] for f in storage_files]
                print(f"Files in bucket: {storage_file_names}")
                
                # If no files, check the bucket
                if not storage_file_names or (len(storage_file_names) == 1 and storage_file_names[0] == '.emptyFolderPlaceholder'):
                    print(f"Bucket empty or only placeholder file. Check '{bucket_name}' bucket.")
                    
                    # Check the bucket
                    buckets = supabase.storage.list_buckets()
                    bucket_names = [bucket.name for bucket in buckets]
                    if bucket_name not in bucket_names:
                        print(f"'{bucket_name}' bucket not found!")
                        return False
                
                # Directly download PDF from Supabase as byte array
                file_found = False
                pdf_filename = None
                
                # 1. First exact match search (filename and clean_filename)
                if filename in storage_file_names:
                    print(f"Exact filename found: {filename}")
                    pdf_filename = filename
                    file_found = True
                elif clean_filename in storage_file_names:
                    print(f"Exact filename found: {clean_filename}")
                    pdf_filename = clean_filename
                    file_found = True
                # 2. Partial match search
                else:
                    for storage_filename in storage_file_names:
                        if storage_filename.lower().endswith('.pdf') and (filename in storage_filename or clean_filename in storage_filename):
                            print(f"Partial match found: {storage_filename}")
                            pdf_filename = storage_filename
                            file_found = True
                            break
                    
                    # 3. Any PDF search
                    if not file_found:
                        print("Exact match not found, searching for any PDF...")
                        for storage_filename in storage_file_names:
                            if storage_filename.lower().endswith('.pdf') and storage_filename != '.emptyFolderPlaceholder':
                                print(f"Alternative PDF found: {storage_filename}")
                                pdf_filename = storage_filename
                                file_found = True
                                break
                
                # File found?
                if file_found and pdf_filename:
                    # Download the file
                    print(f"'{pdf_filename}' downloading...")
                    self.pdf_raw_bytes = supabase.storage.from_(bucket_name).download(pdf_filename)
                    print(f"PDF content downloaded, size: {len(self.pdf_raw_bytes)} byte.")
                else:
                    print(f"File not found. Bucket: {bucket_name}, Searched: {filename} / {clean_filename}")
                    
                    # Suggest using test_bucket.py to upload PDF
                    pdf_test_path = "test_bucket.py"
                    if os.path.exists(pdf_test_path):
                        print(f"You can use '{pdf_test_path}' to upload PDF.")
                    
                    raise Exception(f"PDF file not found: {filename}")
                
            except Exception as e:
                print(f"Supabase storage listing/download error: {str(e)}")
                traceback_str = traceback.format_exc()
                print(f"Error details: {traceback_str}")
                raise e
            
            if not self.pdf_raw_bytes:
                print("PDF content is empty.")
                raise Exception("PDF content not downloaded from Supabase.")
            
            print(f"PDF content downloaded successfully, {len(self.pdf_raw_bytes)} byte.")
            
            # Write PDF content to temporary file
            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                    temp_file.write(self.pdf_raw_bytes)
                    temp_path = temp_file.name
                
                # Extract PDF text
                # Extract PDF content as text
                reader = PdfReader(io.BytesIO(self.pdf_raw_bytes))
                
                # Save page count and text
                self.pdf_text = ""
                self.page_texts = []  # Save page texts separately
                
                for i, page in enumerate(reader.pages):
                    page_text = page.extract_text()
                    self.pdf_text += f"\n--- Page {i+1} ---\n{page_text}\n"
                    self.page_texts.append(page_text)
                
                # Extract images
                self.extract_images_from_bytes(temp_path)
                
                # Reset chat history for new PDF
                self.chat_history = []
                self.create_chat_session()
                
                # Truncate PDF for API
                api_pdf_bytes = self._truncate_pdf_for_api(self.pdf_raw_bytes)
                
                # Get general information about the PDF for model
                self._analyze_pdf_content(api_pdf_bytes)
                
                return True
                
            except Exception as e:
                raise e
                
            finally:
                # Clean up temporary file
                if temp_path and os.path.exists(temp_path):
                    try:
                        os.unlink(temp_path)
                    except Exception as cleanup_error:
                        print(f"Error cleaning up temporary file: {str(cleanup_error)}")
            
        except Exception as e:
            error_msg = f"PDF loading error: {str(e)}"
            print(error_msg)
            return False
    
    def extract_images_from_bytes(self, temp_pdf_path: str):
        """Extracts images from PDF"""
        try:
            # Open PDF with PyMuPDF
            self.pdf_doc = fitz.open(temp_pdf_path)
            
            # Create list of images for each page
            self.page_images = []
            
            for page_index in range(len(self.pdf_doc)):
                page = self.pdf_doc[page_index]
                
                # Extract images from the page
                page_images = []
                
                # Find all image references in the page
                image_list = page.get_images(full=True)
                
                # For each image
                for img_index, img_info in enumerate(image_list):
                    # Extract image from PDF
                    xref = img_info[0]
                    base_image = self.pdf_doc.extract_image(xref)
                    image_bytes = base_image["image"]
                    
                    # Load image as PIL Image
                    image = Image.open(io.BytesIO(image_bytes))
                    
                    # Add image to list (page number, image index, PIL Image)
                    page_images.append((page_index, img_index, image))
                
                # Add page images to main list
                if page_images:
                    self.page_images.append(page_images)
                else:
                    self.page_images.append([])
                
            # Close the file
            self.pdf_doc.close()
            
            print(f"Extracted {sum(len(images) for images in self.page_images)} images from PDF.")
            return True
            
        except Exception as e:
            error_msg = f"Image extraction error: {str(e)}"
            print(error_msg)
            return False
    
    def _analyze_pdf_content(self, pdf_bytes=None):
        """Analyzes PDF content and gets general information"""
        prompt = """
        Create a brief summary of this PDF document.
        Provide information about the title, topic, and main sections of the document.
        Keep the answer short, summarize it in 3-4 sentences.
        """
        
        try:
            # If pdf_bytes is not specified, use self.pdf_raw_bytes
            if pdf_bytes is None:
                pdf_bytes = self.pdf_raw_bytes
                
            # Check PDF size
            if len(pdf_bytes) > 10 * 1024 * 1024:  # If larger than 10MB
                pdf_bytes = self._truncate_pdf_for_api(pdf_bytes)
            
            # Send PDF content and prompt to model
            try:
                response = self.model.generate_content(
                    contents=[
                        {"mime_type": "application/pdf", "data": pdf_bytes},
                        prompt
                    ],
                    stream=False,
                    safety_settings=[
                        {
                            "category": "HARM_CATEGORY_HARASSMENT",
                            "threshold": "BLOCK_NONE"
                        },
                        {
                            "category": "HARM_CATEGORY_HATE_SPEECH",
                            "threshold": "BLOCK_NONE"
                        },
                        {
                            "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                            "threshold": "BLOCK_NONE"
                        },
                        {
                            "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                            "threshold": "BLOCK_NONE"
                        }
                    ]
                )
            except Exception as safety_error:
                print(f"Safety settings error: {str(safety_error)}, trying without safety settings...")
                # Try again without safety settings
                response = self.model.generate_content(
                    contents=[
                        {"mime_type": "application/pdf", "data": pdf_bytes},
                        prompt
                    ],
                    stream=False
                )
            
            # PDF summary created, add to chat history
            self.chat_history.append({"role": "model", "parts": [response.text]})
            
            print("PDF content analyzed.")
            return response.text
            
        except Exception as e:
            error_msg = f"PDF analysis error: {str(e)}"
            print(error_msg)
            print(f"Error details: {traceback.format_exc()}")
            return f"PDF content analysis failed: {error_msg}"
    
    def ask_question(self, question: str, image_bytes: bytes = None, image_mime: str = None) -> str:
        """
        Asks a question about the PDF and returns the answer.
        
        Args:
            question: The question asked
            image_bytes: Binary content of the uploaded image (if any)
            image_mime: MIME type of the image
            
        Returns:
            Answer to the question
        """
        if not self.pdf_raw_bytes:
            return "Please upload a PDF file first."
        
        try:
            # Check PDF size and truncate if necessary
            api_pdf_bytes = self._truncate_pdf_for_api(self.pdf_raw_bytes)
            
            # Create content list
            contents = [{"mime_type": "application/pdf", "data": api_pdf_bytes}]
            
            # If there's an image, add it to the content
            if image_bytes and image_mime:
                try:
                    # Add image to content
                    contents.append({
                        "mime_type": image_mime,
                        "data": image_bytes
                    })
                    
                    # Modify the question
                    prompt = f"{question}\n\nExamine the uploaded image and answer based on the PDF content. Use the information you see in the image."
                except Exception as e:
                    print(f"Image upload error: {str(e)}")
                    prompt = f"{question}\n\nBase your answer on the PDF content."
            else:
                prompt = f"{question}\n\nBase your answer on the PDF content and images in the PDF."
            
            # Add prompt to content
            contents.append(prompt)
            
            # Send content to model
            try:
                response = self.chat_session.send_message(
                    contents,
                    safety_settings=[
                        {
                            "category": "HARM_CATEGORY_HARASSMENT",
                            "threshold": "BLOCK_NONE"
                        },
                        {
                            "category": "HARM_CATEGORY_HATE_SPEECH",
                            "threshold": "BLOCK_NONE"
                        },
                        {
                            "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                            "threshold": "BLOCK_NONE"
                        },
                        {
                            "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                            "threshold": "BLOCK_NONE"
                        }
                    ]
                )
                return response.text
            except Exception as chat_error:
                print(f"Chat session error: {str(chat_error)}")
                print(f"Creating new chat session and retrying...")
                
                # Reset chat session
                self.create_chat_session()
                
                # Try again
                response = self.chat_session.send_message(contents)
                return response.text
            
        except Exception as e:
            error_msg = f"Question asking error: {str(e)}"
            print(error_msg)
            print(f"Error details: {traceback.format_exc()}")
            return f"Question answer failed: {error_msg}"
    
    def generate_quiz(self, num_questions: int = 5) -> str:
        """
        Generates a quiz based on the PDF content.
        
        Args:
            num_questions: Number of questions to generate
            
        Returns:
            Generated quiz (questions and answers)
        """
        if not self.pdf_raw_bytes:
            return "Please upload a PDF file first."
        
        prompt = f"""
        Create a quiz with {num_questions} questions based on the content of this PDF document.
        Specify the correct answer for each question.
        Number the questions and answers.
        """
        
        try:
            # Check PDF size and truncate if necessary
            api_pdf_bytes = self._truncate_pdf_for_api(self.pdf_raw_bytes)
            
            response = self.model.generate_content(
                contents=[
                    {"mime_type": "application/pdf", "data": api_pdf_bytes},
                    prompt
                ],
                safety_settings=[
                    {
                        "category": "HARM_CATEGORY_HARASSMENT",
                        "threshold": "BLOCK_NONE"
                    },
                    {
                        "category": "HARM_CATEGORY_HATE_SPEECH",
                        "threshold": "BLOCK_NONE"
                    },
                    {
                        "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                        "threshold": "BLOCK_NONE"
                    },
                    {
                        "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                        "threshold": "BLOCK_NONE"
                    }
                ]
            )
            
            return response.text
            
        except Exception as e:
            error_msg = f"Quiz generation error: {str(e)}"
            print(error_msg)
            print(f"Error details: {traceback.format_exc()}")
            return f"Quiz generation failed: {error_msg}"
    
    def generate_summary(self, detail_level: str = "medium") -> str:
        """
        Generates a summary of the PDF content.
        
        Args:
            detail_level: Summary detail level (low, medium, high)
            
        Returns:
            Generated summary
        """
        if not self.pdf_raw_bytes:
            return "Please upload a PDF file first."
        
        # Determine length based on detail level
        length_map = {
            "low": "short (1-2 paragraphs)",
            "medium": "medium length (3-4 paragraphs)",
            "high": "detailed (5+ paragraphs)"
        }
        
        length = length_map.get(detail_level.lower(), "medium length (3-4 paragraphs)")
        
        prompt = f"""
        Create a {length} summary of this PDF document.
        Highlight the main headings and important points.
        """
        
        try:
            # Check PDF size and truncate if necessary
            api_pdf_bytes = self._truncate_pdf_for_api(self.pdf_raw_bytes)
            
            response = self.model.generate_content(
                contents=[
                    {"mime_type": "application/pdf", "data": api_pdf_bytes},
                    prompt
                ],
                safety_settings=[
                    {
                        "category": "HARM_CATEGORY_HARASSMENT",
                        "threshold": "BLOCK_NONE"
                    },
                    {
                        "category": "HARM_CATEGORY_HATE_SPEECH",
                        "threshold": "BLOCK_NONE"
                    },
                    {
                        "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                        "threshold": "BLOCK_NONE"
                    },
                    {
                        "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                        "threshold": "BLOCK_NONE"
                    }
                ]
            )
            
            return response.text
            
        except Exception as e:
            error_msg = f"Summary generation error: {str(e)}"
            print(error_msg)
            print(f"Error details: {traceback.format_exc()}")
            return f"Summary generation failed: {error_msg}"
    
    def extract_key_concepts(self) -> str:
        """Extracts key concepts from the PDF"""
        if not self.pdf_raw_bytes:
            return "Please upload a PDF file first."
        
        prompt = """
        List the key concepts and terms in this PDF document.
        Provide a brief explanation for each concept.
        """
        
        try:
            # Check PDF size and truncate if necessary
            api_pdf_bytes = self._truncate_pdf_for_api(self.pdf_raw_bytes)
            
            response = self.model.generate_content(
                contents=[
                    {"mime_type": "application/pdf", "data": api_pdf_bytes},
                    prompt
                ],
                safety_settings=[
                    {
                        "category": "HARM_CATEGORY_HARASSMENT",
                        "threshold": "BLOCK_NONE"
                    },
                    {
                        "category": "HARM_CATEGORY_HATE_SPEECH",
                        "threshold": "BLOCK_NONE"
                    },
                    {
                        "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                        "threshold": "BLOCK_NONE"
                    },
                    {
                        "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                        "threshold": "BLOCK_NONE"
                    }
                ]
            )
            
            return response.text
            
        except Exception as e:
            error_msg = f"Concept extraction error: {str(e)}"
            print(error_msg)
            print(f"Error details: {traceback.format_exc()}")
            return f"Concepts extraction failed: {error_msg}"

# API key from environment variable
api_key = os.environ.get("GOOGLE_API_KEY", "AIzaSyB0SOA4MfPAQQ1aPA8JOSmj4L6oAL0dD2w")
if not api_key:
    api_key = "YOUR_API_KEY"  # !!! DO NOT USE THIS CODE IN PRODUCTION !!!
    # raise ValueError("GEMINI_API_KEY environment variable not set. Please set the API key.")

# Global assistant instance (created when application starts)
assistant = InteractivePDFAssistant(api_key)

def allowed_file(filename, allowed_extensions):
    """Checks if the file extension is one of the allowed extensions"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed_extensions

# Function to determine the MIME type of the uploaded image
def get_image_mime_type(filename):
    """Determines MIME type based on file extension"""
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ""
    
    if ext == 'png':
        return "image/png"
    elif ext == 'gif':
        return "image/gif"
    elif ext == 'webp':
        return "image/webp"
    else:  # jpg, jpeg ve diğerleri
        return "image/jpeg"

# Resmi Supabase üzerinden yükleyen ve işleyen fonksiyon
def upload_and_process_image(file, bucket_name="images"):
    """
    Uploads and processes an image file to Supabase.
    
    Args:
        file: Uploaded file object
        bucket_name: Supabase bucket name
    
    Returns:
        tuple: (image_bytes, mime_type) - Binary content and MIME type of the image
    """
    if file and allowed_file(file.filename, ALLOWED_IMAGE_EXTENSIONS):
        try:
            # Create secure filename
            filename = secure_filename(file.filename)
            
            # Create unique filename
            unique_filename = f"{int(time.time())}_{filename}"
            
            # Read the file
            file_content = file.read()
            
            # Upload file to Supabase Storage
            supabase.storage.from_(bucket_name).upload(
                file=file_content,
                path=unique_filename,
                file_options={"content-type": get_image_mime_type(filename)}
            )
            
            # Add record to DB
            response = supabase.table("images").insert({
                "file_name": filename,
                "file_path": f"{bucket_name}/{unique_filename}"
            }).execute()
            
            # Determine MIME type of the image
            mime_type = get_image_mime_type(filename)
            
            return file_content, mime_type
            
        except Exception as e:
            print(f"Image upload error: {str(e)}")
            return None, None
    
    return None, None

@app.route('/')
def index():
    """Ana sayfa"""
    try:
        # Supabase'den tüm PDF kayıtlarını getir
        pdf_files = get_all_pdfs()
        
        print(f"Ana sayfa: {len(pdf_files)} PDF dosyası bulundu.")
        
        # PDF dosyalarını listelemek için şablonu göster
        return render_template('index.html', pdf_files=pdf_files)
    except Exception as e:
        error_msg = f"Ana sayfa yüklenirken hata oluştu: {str(e)}"
        print(error_msg)
        traceback_str = traceback.format_exc()
        print(f"Hata ayrıntıları: {traceback_str}")
        
        # Hataya rağmen sayfayı göster
        return render_template('index.html', pdf_files=[], error_message=error_msg)

@app.route('/select_pdf', methods=['POST'])
def select_pdf():
    """
    Processes the PDF selected by the user.
    If a file is selected or uploaded, initializes the PDF assistant.
    """
    try:
        print("select_pdf route başlatıldı...")
        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            print("API anahtarı bulunamadı!")
            return jsonify({"error": "API key not found. Check your .env file."}), 500
        
        print(f"API anahtarı bulundu: {api_key[:5]}...{api_key[-5:]}")
        
        # Create assistant object and save to session
        if 'pdf_assistant' not in session:
            session['pdf_assistant'] = {}
        
        print(f"Request form: {list(request.form.keys())}")
        print(f"Request files: {list(request.files.keys()) if request.files else 'Dosya yok'}")
        
        if 'file' in request.files:
            # User is uploading a file
            file = request.files['file']
            print(f"Yüklenen dosya: {file.filename if file and file.filename else 'İsim yok'}")
            
            if file and file.filename and allowed_file(file.filename, ALLOWED_EXTENSIONS):
                # Create a secure filename
                filename = secure_filename(file.filename)
                print(f"Güvenli dosya adı: {filename}")
                
                # Upload file to Supabase
                pdf_data = upload_pdf_to_supabase(file, filename)
                print(f"PDF veri dönüşü: {pdf_data}")
                
                if not pdf_data:
                    print("Supabase'e yükleme başarısız!")
                    return jsonify({"error": "Upload to Supabase failed."}), 500
                
                # Başarılı yükleme yanıtı - asenkron işlemeyi simüle et
                session['current_pdf_id'] = pdf_data['id']
                session['pdf_assistant'] = {
                    'pdf_id': pdf_data['id'],
                    'title': filename,
                    'chat_history': []
                }
                
                return jsonify({
                    "success": True,
                    "message": f"PDF uploaded: {filename}",
                    "pdf_id": pdf_data['id'],
                    "pdf_title": filename,
                    "status": "loading"
                })
            else:
                print("Geçersiz dosya türü veya boş dosya!")
                return jsonify({"error": "Invalid file type. Please upload a PDF."}), 400
        
        elif 'select_existing' in request.form:
            # Kullanıcı mevcut bir PDF seçiyor
            pdf_id = request.form.get('pdf_id')
            print(f"Seçilen PDF ID: {pdf_id}")
            
            if not pdf_id:
                print("PDF ID sağlanmadı!")
                return jsonify({"error": "PDF ID not provided."}), 400
            
            try:
                # Supabase'den PDF bilgilerini al
                print(f"PDF ID: {pdf_id} sorgulanıyor...")
                
                try:
                    response = supabase.table("pdfs").select("*").eq("id", pdf_id).execute()
                    print(f"Sorgu yanıtı: {response}")
                except Exception as db_err:
                    print(f"Supabase sorgu hatası: {str(db_err)}")
                    return jsonify({"error": f"Database query error: {str(db_err)}"}), 500
                
                if not response.data:
                    print(f"PDF ID: {pdf_id} bulunamadı.")
                    return jsonify({"error": "PDF not found."}), 404
                
                pdf_record = response.data[0]
                filename = pdf_record["file_name"]
                print(f"PDF bulundu: {filename}")
                
                # Session'da asistan bilgilerini sakla - asenkron işleme için hemen yanıt ver
                session['pdf_assistant'] = {
                    'pdf_id': pdf_id,
                    'title': filename,
                    'chat_history': []
                }
                session['current_pdf_id'] = pdf_id
                
                # Başarılı yanıt döndür - asistanı arka planda yükle, ön yüzde gösterilebilir
                return jsonify({
                    "success": True,
                    "message": f"PDF selected: {filename}",
                    "pdf_id": pdf_id,
                    "pdf_title": filename,
                    "status": "loading"
                })
                
            except Exception as e:
                error_msg = f"PDF selection error: {str(e)}"
                print(error_msg)
                traceback_str = traceback.format_exc()
                print(f"Error details: {traceback_str}")
                return jsonify({"error": error_msg}), 500
        else:
            print(f"Geçersiz istek: form={request.form}, files={request.files}")
            return jsonify({"error": "Invalid request content. No file or PDF ID provided."}), 400
    except Exception as e:
        error_msg = f"PDF processing error: {str(e)}"
        print(error_msg)
        traceback_str = traceback.format_exc()
        print(f"Error details: {traceback_str}")
        return jsonify({"error": error_msg}), 500
    
    return jsonify({"error": "Invalid request content."}), 400

@app.route('/chat', methods=['POST'])
def chat():
    """
    Interactive chat API with the PDF.
    
    Takes 'question' and optional 'image' parameter as JSON or form data.
    Returns error if no PDF is loaded.
    """
    if request.method == 'POST':
        try:
            # Get API key
            api_key = os.environ.get("GOOGLE_API_KEY")
            if not api_key:
                return jsonify({"error": "API key not found. Check your .env file."}), 500
            
            # Get assistant info from session
            pdf_info = session.get('pdf_assistant')
            if not pdf_info:
                return jsonify({"error": "You need to select a PDF first."}), 400
            
            # Get question from JSON or form data
            if request.is_json:
                data = request.get_json()
                question = data.get('question', '')
                conversation_mode = data.get('mode', 'chat')
            else:
                question = request.form.get('question', '')
                conversation_mode = request.form.get('mode', 'chat')
            
            if not question and conversation_mode == 'chat':
                return jsonify({"error": "Question cannot be empty."}), 400
            
            # Yüklenen resim varsa işle
            image_bytes = None
            image_mime = None
            if 'image' in request.files:
                image_file = request.files['image']
                if image_file and image_file.filename and allowed_file(image_file.filename, ALLOWED_IMAGE_EXTENSIONS):
                    # Resmi Supabase'e yükle ve binary verisini al
                    image_bytes, image_mime = upload_and_process_image(image_file)
            
            # Asistanı oluştur ve PDF'i yükle
            assistant = InteractivePDFAssistant(api_key)
            current_pdf_id = session.get('current_pdf_id')
            assistant.load_pdf_from_supabase(current_pdf_id, pdf_info['title'])
            
            # Chat history ayarla
            if 'chat_history' in pdf_info:
                assistant.chat_history = pdf_info['chat_history']
            
            # İstenen işlemi gerçekleştir
            if conversation_mode == 'chat':
                # Soru-cevap modu
                answer = assistant.ask_question(question, image_bytes, image_mime)
                
                # Soru ve cevabı Supabase'e kaydet
                if current_pdf_id:
                    save_qa_session(current_pdf_id, question, answer)
                
                # Session'da chat history'ni güncelle
                pdf_info['chat_history'] = assistant.chat_history
                session['pdf_assistant'] = pdf_info
                
                return jsonify({
                    "success": True, 
                    "answer": answer,
                    "mode": "chat"
                })
                
            elif conversation_mode == 'generate_quiz':
                # Quiz oluşturma
                num_questions = int(request.form.get('num_questions', 5))
                quiz_content = assistant.generate_quiz(num_questions)
                
                # Quiz içeriğini Supabase'e kaydet
                if current_pdf_id:
                    save_generated_content(current_pdf_id, "quiz", quiz_content)
                
                return jsonify({
                    "success": True, 
                    "answer": quiz_content,
                    "mode": "generate_quiz"
                })
                
            elif conversation_mode == 'generate_summary':
                # Özet oluşturma
                detail_level = request.form.get('detail_level', 'medium')
                summary_content = assistant.generate_summary(detail_level)
                
                # Özet içeriğini Supabase'e kaydet
                if current_pdf_id:
                    save_generated_content(current_pdf_id, "summary", summary_content)
                
                return jsonify({
                    "success": True, 
                    "answer": summary_content,
                    "mode": "generate_summary"
                })
                
            elif conversation_mode == 'extract_key_concepts':
                # Anahtar kavramları çıkarma
                concepts_content = assistant.extract_key_concepts()
                
                # Kavramları Supabase'e kaydet
                if current_pdf_id:
                    save_generated_content(current_pdf_id, "key_concepts", concepts_content)
                
                return jsonify({
                    "success": True, 
                    "answer": concepts_content,
                    "mode": "extract_key_concepts"
                })
            
            else:
                return jsonify({"error": "Invalid mode."}), 400
                
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            return jsonify({
                "success": False, 
                "error": str(e),
                "details": error_details
            }), 500
    
    return jsonify({"error": "Unsupported method."}), 405

@app.route('/uploads/<path:filename>')
def serve_image(filename):
    """Güvenli bir şekilde yüklenen resmi sunar"""
    filename = secure_filename(filename)
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# PDF'i Supabase'e yükleyen ve kaydeden fonksiyon
def upload_pdf_to_supabase(file, filename):
    """
    Uploads a PDF file to Supabase and saves it to the database.
    
    Args:
        file: Uploaded file object
        filename: Secure filename
    
    Returns:
        dict: ID and file path of the created PDF record
    """
    try:
        # Create a unique timestamp
        timestamp = int(time.time())
        
        # Read the file
        file_content = file.read()
        
        # Check if a file with the same name already exists
        try:
            # Delete if exists
            supabase.storage.from_("pdfs").remove([filename])
            print(f"Previous '{filename}' file deleted.")
        except:
            # Continue if not
            pass
            
        # Upload file to Supabase Storage
        supabase.storage.from_("pdfs").upload(
            file=file_content,
            path=filename, # Save with original filename
            file_options={"content-type": "application/pdf"}
        )
        
        # If a record with the same name exists, update, otherwise insert new
        try:
            # First get existing record
            query_response = supabase.table("pdfs").select("id").eq("file_name", filename).execute()
            
            if query_response.data and len(query_response.data) > 0:
                # Update existing record
                existing_id = query_response.data[0]["id"]
                # updated_at column not updated if not found
                try:
                    update_response = supabase.table("pdfs").update({
                        "updated_at": timestamp
                    }).eq("id", existing_id).execute()
                except:
                    print(f"updated_at column not found, cannot update.")
                
                pdf_id = existing_id
            else:
                # Insert new record (created_at and updated_at not added)
                insert_response = supabase.table("pdfs").insert({
                    "file_name": filename,
                    "file_path": f"pdfs/{filename}",
                    "title": filename.replace(".pdf", ""),
                    "description": "PDF file"
                }).execute()
                
                pdf_id = insert_response.data[0]["id"]
        except Exception as e:
            # Try inserting new record if query error
            print(f"PDF record query error: {str(e)}")
            try:
                insert_response = supabase.table("pdfs").insert({
                    "file_name": filename,
                    "file_path": f"pdfs/{filename}",
                    "title": filename.replace(".pdf", ""),
                    "description": "PDF file" 
                }).execute()
                
                pdf_id = insert_response.data[0]["id"]
            except Exception as insert_error:
                print(f"PDF record insertion error: {str(insert_error)}")
                # Fallback to a random ID
                pdf_id = str(uuid.uuid4())
        
        return {"id": pdf_id, "file_path": f"pdfs/{filename}"}
        
    except Exception as e:
        print(f"PDF upload error: {str(e)}")
        return None

# Soru-cevap oturumunu Supabase'e kaydeden fonksiyon
def save_qa_session(pdf_id, question, answer):
    """
    Saves a question-answer session to Supabase.
    
    Args:
        pdf_id: ID of the PDF record
        question: Question asked
        answer: Answer given
    
    Returns:
        dict: The created QA record
    """
    try:
        response = supabase.table("qa_sessions").insert({
            "pdf_id": pdf_id,
            "question": question,
            "answer": answer
            # If using authentication, add user_id
            # "user_id": session.get("user_id")
        }).execute()
        
        return response.data[0] if response.data else None
        
    except Exception as e:
        print(f"QA saving error: {str(e)}")
        return None

# Üretilen içeriği Supabase'e kaydeden fonksiyon
def save_generated_content(pdf_id, content_type, content):
    """
    Saves generated content to Supabase.
    
    Args:
        pdf_id: ID of the PDF record
        content_type: Content type ('summary', 'quiz', 'key_concepts')
        content: Generated content
    
    Returns:
        dict: The created content record
    """
    try:
        response = supabase.table("generated_content").insert({
            "pdf_id": pdf_id,
            "content_type": content_type,
            "content": content
            # If using authentication, add user_id
            # "user_id": session.get("user_id")
        }).execute()
        
        return response.data[0] if response.data else None
        
    except Exception as e:
        print(f"Content saving error: {str(e)}")
        return None

# Pdfs tablosunu kontrol eden ve oluşturan fonksiyon
def ensure_pdfs_table_exists():
    """Pdfs tablosunu oluştur ve gerekli sütunların var olduğundan emin ol"""
    try:
        # Directly query pdfs table, will error if not exists
        try:
            print("Checking pdfs table...")
            table_check = supabase.table("pdfs").select("*").limit(1).execute()
            print("pdfs table exists.")
            return True
        except Exception as e:
            print(f"pdfs table query error: {str(e)}")
            
            print("Creating pdfs table (simple method)...")
            # En basit şekilde tablo oluştur
            try:
                # Try insert with minimum data
                supabase.table("pdfs").insert({
                    "file_name": "_temp", 
                    "file_path": "_temp"
                }).execute()
                
                # Success, delete temporary record
                supabase.table("pdfs").delete().eq("file_name", "_temp").execute()
                print("pdfs table created successfully.")
                return True
            except Exception as e:
                print(f"Table creation error: {str(e)}")
                return False
    except Exception as e:
        print(f"Table check error: {str(e)}")
        return False

# Images tablosunu kontrol eden ve oluşturan fonksiyon
def ensure_images_table_exists():
    """Images tablosunu oluştur ve gerekli sütunların var olduğundan emin ol"""
    try:
        # Directly query images table, will error if not exists
        try:
            print("Checking images table...")
            table_check = supabase.table("images").select("*").limit(1).execute()
            print("images table exists.")
            return True
        except Exception as e:
            print(f"images table query error: {str(e)}")
            
            print("Creating images table (simple method)...")
            # En basit şekilde tablo oluştur
            try:
                # Try insert with minimum data
                supabase.table("images").insert({
                    "file_name": "_temp", 
                    "file_path": "_temp"
                }).execute()
                
                # Success, delete temporary record
                supabase.table("images").delete().eq("file_name", "_temp").execute()
                print("images table created successfully.")
                return True
            except Exception as e:
                print(f"Table creation error: {str(e)}")
                return False
    except Exception as e:
        print(f"Table check error: {str(e)}")
        return False

# Mevcut PDF kayıtlarını getiren fonksiyon
def get_all_pdfs():
    """
    Tüm PDF kayıtlarını getirir.
    
    Returns:
        list: PDF kayıtlarının listesi
    """
    try:
        # Önce tablo var mı kontrol et
        ensure_pdfs_table_exists()
        
        # Önce Supabase'den PDF tablosunu sorgula
        response = supabase.table("pdfs").select("*").execute()
        print(f"PDF records queried, {len(response.data)} records found.")
        
        if not response.data or len(response.data) == 0:
            print("PDF records empty, checking Storage for files...")
            # Eğer veritabanında kayıt yoksa, storage'daki dosyaları kontrol et
            try:
                storage_response = supabase.storage.from_("pdfs").list()
                print(f"Storage has {len(storage_response)} files.")
                
                # Sadece PDF dosyalarını filtrele
                pdf_files = []
                for file in storage_response:
                    name = file.get("name")
                    if name and name.lower().endswith('.pdf') and name != '.emptyFolderPlaceholder':
                        pdf_files.append(file)
                
                print(f"Storage has {len(pdf_files)} PDF files.")
                
                # Storage'da dosya varsa veritabanına ekle
                if pdf_files:
                    added_count = 0
                    for file in pdf_files:
                        filename = file.get("name")
                        if filename:
                            print(f"PDF file found: {filename}, adding to database...")
                            # Veritabanına kayıt ekle
                            try:
                                insert_response = supabase.table("pdfs").insert({
                                    "file_name": filename,
                                    "file_path": f"pdfs/{filename}",
                                    "title": filename.replace(".pdf", ""),
                                    "description": "PDF file"
                                    # created_at column error, removed
                                }).execute()
                                print(f"{filename} added to database.")
                                added_count += 1
                            except Exception as e:
                                print(f"PDF record insertion error: {str(e)}")
                    
                    print(f"Total {added_count} PDF files added to database.")
                    
                    # Tekrar PDF tablosunu sorgula
                    response = supabase.table("pdfs").select("*").execute()
                    print(f"PDF records queried again, {len(response.data)} records found.")
            except Exception as e:
                print(f"Storage PDF search error: {str(e)}")
                traceback_str = traceback.format_exc()
                print(f"Error details: {traceback_str}")
        
        return response.data
    except Exception as e:
        print(f"PDF records retrieval error: {str(e)}")
        traceback_str = traceback.format_exc()
        print(f"Error details: {traceback_str}")
        return []

# CORS başlıkları
@app.after_request
def add_cors_headers(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

# Vercel için gerekli
if __name__ == '__main__':
    # Gerekli klasörlerin varlığını kontrol et, yoksa oluştur
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
        print(f"{app.config['UPLOAD_FOLDER']} directory created.")
    
    # Supabase bağlantısını ve bucket'ları kontrol et
    try:
        print("Supabase connection check...")
        
        # Bucket'ı kontrol et ve yoksa oluştur
        try:
            # Mevcut bucket'ları listele
            buckets = supabase.storage.list_buckets()
            bucket_names = [bucket.name for bucket in buckets]
            print(f"Existing buckets: {bucket_names}")
            
            # PDF bucket'ı kontrolü
            bucket_name = "pdfs"
            
            # pdfs bucket'ı yoksa oluştur
            if bucket_name not in bucket_names:
                print(f"'{bucket_name}' bucket not found. Creating...")
                supabase.storage.create_bucket(
                    id=bucket_name, 
                    options={"public": True}
                )
                print(f"'{bucket_name}' bucket created.")
            
            # Public erişim ayarlarını kontrol et
            supabase.storage.update_bucket(bucket_name, {"public": True})
            print(f"'{bucket_name}' bucket set to public.")
            
            # Images bucket'ı kontrolü
            bucket_name = "images"
            
            # images bucket'ı yoksa oluştur
            if bucket_name not in bucket_names:
                print(f"'{bucket_name}' bucket not found. Creating...")
                supabase.storage.create_bucket(
                    id=bucket_name, 
                    options={"public": True}
                )
                print(f"'{bucket_name}' bucket created.")
            
            # Public erişim ayarlarını kontrol et
            supabase.storage.update_bucket(bucket_name, {"public": True})
            print(f"'{bucket_name}' bucket set to public.")
            
            # Tabloyu kontrol et
            ensure_pdfs_table_exists()
            
            # Images tablosunu kontrol et
            ensure_images_table_exists()
            
            # Dosyaları kontrol et ve DB'ye ekle
            get_all_pdfs()
            
        except Exception as e:
            print(f"Bucket check error: {str(e)}")
    except Exception as e:
        print(f"Supabase connection error: {str(e)}")
    
    app.run(debug=True, threaded=True, host='0.0.0.0') 

# PDF yükleme durumunu kontrol eden yeni endpoint
@app.route('/pdf_load_status', methods=['GET'])
def pdf_load_status():
    """PDF yükleme durumunu kontrol eder"""
    pdf_id = request.args.get('pdf_id')
    
    if not pdf_id:
        return jsonify({"error": "PDF ID not provided"}), 400
    
    # Session'da saklanan PDF bilgilerini kontrol et
    pdf_info = session.get('pdf_assistant', {})
    current_pdf_id = session.get('current_pdf_id')
    
    if not pdf_info or not current_pdf_id or current_pdf_id != pdf_id:
        return jsonify({
            "success": False,
            "status": "not_found",
            "message": "PDF not selected or session expired"
        })
    
    # PDF asistanı manuel olarak başlatmak için
    try:
        # Supabase'den PDF bilgilerini al
        response = supabase.table("pdfs").select("*").eq("id", pdf_id).execute()
        
        if not response.data:
            return jsonify({"success": False, "status": "not_found", "message": "PDF not found in database"})
        
        pdf_record = response.data[0]
        filename = pdf_record["file_name"]
        
        # Burada asistanı başlatmaya çalışıyoruz
        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            return jsonify({"success": False, "status": "error", "message": "API key not found"})
        
        try:
            # Asistanı oluştur ve PDF'i yüklemeye çalış
            assistant = InteractivePDFAssistant(api_key)
            load_success = assistant.load_pdf_from_supabase(pdf_id, filename)
            
            if load_success:
                return jsonify({
                    "success": True,
                    "status": "ready",
                    "message": f"PDF loaded: {filename}"
                })
            else:
                return jsonify({
                    "success": False,
                    "status": "error",
                    "message": "PDF loading failed"
                })
                
        except Exception as load_err:
            print(f"PDF yükleme hatası: {str(load_err)}")
            traceback_str = traceback.format_exc()
            print(f"Yükleme hata ayrıntıları: {traceback_str}")
            
            return jsonify({
                "success": False,
                "status": "error",
                "message": f"PDF loading error: {str(load_err)}"
            })
        
    except Exception as e:
        error_msg = f"Status check error: {str(e)}"
        print(error_msg)
        traceback_str = traceback.format_exc()
        print(f"Error details: {traceback_str}")
        
        return jsonify({
            "success": False,
            "status": "error",
            "message": error_msg
        })

