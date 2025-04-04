document.addEventListener('DOMContentLoaded', () => {
    // DOM References
    const pdfList = document.getElementById('pdf-list-container');
    const togglePdfListBtn = document.getElementById('toggle-pdf-list');
    const chatForm = document.getElementById('chat-form');
    const messageInput = document.getElementById('message-input');
    const sendBtn = document.getElementById('send-btn');
    const chatMessages = document.getElementById('chat-messages');
    const chatContainer = document.getElementById('chat-container');
    const currentPdfTitle = document.getElementById('current-pdf-title');
    const loadingSpinner = document.getElementById('loading-spinner');
    const cmdButtons = document.querySelectorAll('.cmd-btn');
    
    // Image upload DOM references
    const imageUploadInput = document.getElementById('image-upload');
    const imagePreviewContainer = document.getElementById('image-preview-container');
    const imagePreview = document.getElementById('image-preview');
    const clearImageBtn = document.getElementById('clear-image-btn');
    
    // Current image file
    let currentImage = null;
    
    // PDF List Toggle Function
    togglePdfListBtn.addEventListener('click', () => {
        togglePdfListBtn.classList.toggle('active');
        pdfList.classList.toggle('hidden');
    });
    
    // PDF Selection Function
    document.querySelectorAll('.pdf-select-btn').forEach(button => {
        button.addEventListener('click', async (e) => {
            const filename = e.currentTarget.dataset.filename;
            const pdfId = e.currentTarget.dataset.pdfId;
            await selectPdf(filename, pdfId);
        });
    });
    
    // Command Buttons Function
    cmdButtons.forEach(button => {
        button.addEventListener('click', () => {
            const cmd = button.dataset.cmd;
            if (messageInput.disabled) return;
            
            messageInput.value = cmd;
            sendMessage();
        });
    });
    
    // Image Upload Function
    imageUploadInput.addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (!file) return;
        
        // Accept only image files
        if (!file.type.startsWith('image/')) {
            showToast('Please select a valid image file', 'error');
            return;
        }
        
        // File size check (should not be larger than 5MB)
        if (file.size > 5 * 1024 * 1024) {
            showToast('Image file should be less than 5MB', 'error');
            return;
        }
        
        // Preview the image
        const reader = new FileReader();
        reader.onload = function(e) {
            imagePreview.src = e.target.result;
            imagePreview.classList.remove('hidden');
            clearImageBtn.classList.remove('hidden');
            imagePreviewContainer.classList.add('active');
            
            // Store the image
            currentImage = {
                data: e.target.result,
                file: file
            };
        };
        reader.readAsDataURL(file);
    });
    
    // Clear Image Function
    clearImageBtn.addEventListener('click', () => {
        clearImageUpload();
    });
    
    function clearImageUpload() {
        imagePreview.src = '';
        imagePreview.classList.add('hidden');
        clearImageBtn.classList.add('hidden');
        imagePreviewContainer.classList.remove('active');
        imageUploadInput.value = '';
        currentImage = null;
    }
    
    // Sohbet Formu Gönderme İşlevi
    chatForm.addEventListener('submit', (e) => {
        e.preventDefault();
        sendMessage();
    });
    
    // PDF Seçme Fonksiyonu
    async function selectPdf(filename, pdfId) {
        try {
            showLoading();
            
            const formData = new FormData();
            formData.append('select_existing', 'true');
            formData.append('pdf_id', pdfId);
            
            const response = await fetch('/select_pdf', {
                method: 'POST',
                body: formData
            });
            
            const data = await response.json();
            
            if (data.success) {
                // Remove selected class from all PDF buttons
                document.querySelectorAll('.pdf-item').forEach(item => {
                    item.classList.remove('selected');
                });
                
                // Mark selected PDF
                const selectedItem = document.querySelector(`[data-pdf-id="${pdfId}"]`).parentElement;
                selectedItem.classList.add('selected');
                
                // Enable chat area
                chatContainer.classList.remove('disabled');
                messageInput.disabled = false;
                sendBtn.disabled = false;
                
                // Enable command buttons
                cmdButtons.forEach(btn => btn.disabled = false);
                
                // Update PDF title
                if (currentPdfTitle) {
                    currentPdfTitle.textContent = filename;
                }
                
                // Clear current chat and add welcome message
                chatMessages.innerHTML = '';
                addMessage('assistant', `Hello! You can ask questions about <strong>${filename}</strong> file.\n\nYou can upload an image to ask questions related to the information in the PDF.`);
                
                // Show toast notification
                showToast(`${filename} successfully loaded.`, 'success');
                
                // Close PDF list in mobile view
                if (window.innerWidth < 768) {
                    togglePdfListBtn.classList.remove('active');
                    pdfList.classList.add('hidden');
                }
            } else {
                showToast(`Error: ${data.error}`, 'error');
            }
        } catch (err) {
            console.error('PDF selection error:', err);
            showToast('An error occurred while selecting the PDF.', 'error');
        } finally {
            hideLoading();
        }
    }
    
    // Mesaj Gönderme Fonksiyonu
    async function sendMessage() {
        const message = messageInput.value.trim();
        if (!message && !currentImage) return;
        
        try {
            // Show loading
            showLoading();
            
            // Create FormData
            const formData = new FormData();
            
            // Add message if exists
            if (message) {
                formData.append('question', message);
                // Add user message to UI
                addMessage('user', message);
            }
            
            // Add image if exists
            let imageHTML = '';
            if (currentImage) {
                formData.append('image', currentImage.file);
                // Add user message with image to UI
                imageHTML = `<img src="${currentImage.data}" alt="Image uploaded by user">`;
                
                if (!message) {
                    addMessage('user', imageHTML);
                } else {
                    // Get the last added message and add the image
                    const lastMessage = chatMessages.lastElementChild;
                    const contentDiv = lastMessage.querySelector('.message-content');
                    contentDiv.innerHTML += '<br>' + imageHTML;
                }
            }
            
            // Clear input
            messageInput.value = '';
            clearImageUpload();
            
            // Send message to server
            const response = await fetch('/chat', {
                method: 'POST',
                body: formData
            });
            
            // Check if response is JSON
            const contentType = response.headers.get('content-type');
            if (contentType && contentType.includes('application/json')) {
                const data = await response.json();
                
                if (data.success) {
                    // Add assistant response to UI
                    addMessage('assistant', data.answer, new Date().toLocaleTimeString());
                    
                    // Special processing based on response type
                    if (data.mode === 'generate_summary') {
                        // Special formatting for summary
                        const summaryMessage = chatMessages.lastElementChild;
                        summaryMessage.classList.add('summary');
                    } else if (data.mode === 'generate_quiz') {
                        // Special formatting for quiz
                        const quizMessage = chatMessages.lastElementChild;
                        quizMessage.classList.add('quiz');
                    } else if (data.mode === 'extract_key_concepts') {
                        // Special formatting for concepts
                        const conceptsMessage = chatMessages.lastElementChild;
                        conceptsMessage.classList.add('concepts');
                    }
                } else {
                    showToast(`Error: ${data.error}`, 'error');
                }
            } else {
                const errorText = await response.text();
                showToast(`Error: ${errorText}`, 'error');
            }
        } catch (err) {
            console.error('Message sending error:', err);
            showToast('An error occurred while sending the message.', 'error');
        } finally {
            hideLoading();
            // Focus on input
            messageInput.focus();
        }
    }
    
    // Message Adding Function
    function addMessage(sender, content, timestamp = null) {
        // If no timestamp, use current time
        if (!timestamp) {
            const now = new Date();
            timestamp = now.toLocaleTimeString();
        }
        
        // Create message HTML
        const messageElement = document.createElement('div');
        messageElement.className = `message ${sender}`;
        
        // Check if there's an image tag (no preprocessing)
        const hasImage = content.includes('<img');
        
        // Format content (if no image)
        const formattedContent = hasImage ? content : formatContent(content);
        
        messageElement.innerHTML = `
            <div class="message-content">${formattedContent}</div>
            <span class="message-time">${timestamp}</span>
        `;
        
        // Add message to chat area
        chatMessages.appendChild(messageElement);
        
        // Scroll chat area down
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }
    
    // Content Formatting Function
    function formatContent(content) {
        // Convert newline characters to <br> tags
        let formatted = content.replace(/\n/g, '<br>');
        
        // Markdown-like formatting: **bold** text
        formatted = formatted.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
        
        // Markdown-like formatting: *italic* text
        formatted = formatted.replace(/\*(.*?)\*/g, '<em>$1</em>');
        
        // Process code blocks
        formatted = formatted.replace(/```([\s\S]*?)```/g, function(match, p1) {
            return `<pre class="code-block">${p1}</pre>`;
        });
        
        // Process single-line code blocks
        formatted = formatted.replace(/`(.*?)`/g, '<code>$1</code>');
        
        return formatted;
    }
    
    // Loading Indicator
    function showLoading() {
        loadingSpinner.classList.remove('hidden');
    }
    
    function hideLoading() {
        loadingSpinner.classList.add('hidden');
    }
    
    // Show Toast Notification
    function showToast(message, type = 'info') {
        const toastContainer = document.getElementById('toast-container');
        
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        toast.innerHTML = `
            <i class="fa fa-${type === 'error' ? 'exclamation-circle' : 'check-circle'}"></i>
            <span>${message}</span>
        `;
        
        toastContainer.appendChild(toast);
        
        // Remove notification after 3 seconds
        setTimeout(() => {
            toast.style.opacity = '0';
            setTimeout(() => {
                toastContainer.removeChild(toast);
            }, 300);
        }, 3000);
    }
}); 