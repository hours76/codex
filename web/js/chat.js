// DOM elements
const messageInput = document.getElementById('message-input');
const sendButton = document.getElementById('send-button');
const scheduleButton = document.getElementById('schedule-button');
const clearAllTasksButton = document.getElementById('clear-all-tasks');
const scheduleTimeInput = document.getElementById('schedule-time');
const scheduleMessageInput = document.getElementById('schedule-message');
const tasksList = document.getElementById('tasks-list');
const taskCount = document.getElementById('task-count');
const activeSessionName = document.getElementById('active-session-name');
const tabList = document.getElementById('tab-list');
const chatSessions = document.getElementById('chat-sessions');

// Session management
let sessions = {};
let activeSessionId = null;  // Will be set to unique timestamp
let nextSessionId = null;    // Will be set to unique timestamp + 1
let sessionRecoveryAttempted = false;

// Sessions will be initialized in initializePage()

// Get base path from global variable (injected by server)
function getBasePath() {
    return window.BASE_PATH || '';
}

// Helper function to get cookie value
function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(';').shift();
    return null;
}

// Helper function to create path-aware API URLs
function apiUrl(path) {
    const basePath = getBasePath();
    return `${basePath}${path}`;
}

// Initialize session for HTTP request/response
function connectSession(sessionId) {
    // Update tab and session name to show ready state
    const last4Digits = sessionId.toString().slice(-4);
    const sessionName = `Chat ${last4Digits}`;
    
    const tab = document.querySelector(`[data-session="${sessionId}"].tab .tab-title`);
    if (tab && tab.textContent.includes('Init...')) {
        tab.textContent = sessionName;
    }
    
    // Create session object if it doesn't exist
    if (!sessions[sessionId]) {
        sessions[sessionId] = {};
    }
    
    sessions[sessionId].name = sessionName;
    sessions[sessionId].connected = true;
    
    // Update UI if this is the active session
    if (sessionId === activeSessionId) {
        messageInput.disabled = false;
        sendButton.disabled = false;
    }
}

// Attempt session recovery from server
async function attemptSessionRecovery() {
    if (sessionRecoveryAttempted) return;
    sessionRecoveryAttempted = true;
    
    try {
        const response = await fetch(apiUrl('/web/sessions'), {
            credentials: 'same-origin'
        });
        const data = await response.json();
        if (data.sessions && data.sessions.length > 0) {
            
            // Recover all sessions found on the server (including empty ones)
            let meaningfulSessions = data.sessions;
            
            // Proceed with recovery if any sessions exist
            if (meaningfulSessions.length > 0) {
                
                // Remember currently active session before clearing (from localStorage)
                const previousActiveSession = localStorage.getItem('lastActiveSession');
                console.log('Previous active session from localStorage:', previousActiveSession);
                
                // Clear current session and UI
                sessions = {};
                
                // Remove existing tabs and chat sessions
                const existingTabs = document.querySelectorAll('.tab');
                const existingChatSessions = document.querySelectorAll('.chat-session');
                existingTabs.forEach(tab => tab.remove());
                existingChatSessions.forEach(session => session.remove());
                
                // Recreate tabs and sessions from meaningful server data
                let maxSessionId = 0;
                let hasActiveSessions = false;
                
                for (const sessionInfo of meaningfulSessions) {
                    const sessionId = parseInt(sessionInfo.session_id);
                    maxSessionId = Math.max(maxSessionId, sessionId);
                    
                    // Create session object
                    sessions[sessionId] = {
                        id: sessionId,
                        name: `Chat ${sessionId}`,
                        existsOnServer: true  // Recovered from server
                    };
                    
                    // Create tab for this session
                    createTabElement(sessionId);
                    createChatSessionElement(sessionId);
                    
                    // Update session UI to show it's ready
                    connectSession(sessionId);
                    
                    // Set as active session if it was the previously active one, or if no active session set yet
                    if (sessionId === parseInt(previousActiveSession)) {
                        console.log('Restoring previous active session:', sessionId);
                        activeSessionId = sessionId;
                        hasActiveSessions = true;
                    } else if (!hasActiveSessions) {
                        console.log('Setting first session as active:', sessionId);
                        activeSessionId = sessionId;
                        hasActiveSessions = true;
                    }
                }
                
                nextSessionId = maxSessionId + 1;
                switchToTab(activeSessionId);
                showNotification(`Recovered ${meaningfulSessions.length} session(s) from server`, 'success');
            }
        }
    } catch (error) {
        console.error('Session recovery failed:', error);
        // Continue with default session
    }
}

// Load chat history for a session from server
async function loadSessionHistory(sessionId) {
    try {
        // IMPORTANT: Clear client cache FIRST before making API call
        // Client cache and server history are mutually exclusive
        if (sessions[sessionId]) {
            sessions[sessionId].messages = [];  // Clear client cache immediately
            
            // Clear the chat area to prevent duplicates
            const chatArea = document.getElementById(`chat-area-${sessionId}`);
            if (chatArea) {
                chatArea.innerHTML = '';
            }
        }
        
        const response = await fetch(apiUrl(`/web/sessions/${sessionId}/history`), {
            credentials: 'same-origin'
        });
        
        if (response.ok) {
            const data = await response.json();
            const history = data.history || [];
            
            if (sessions[sessionId]) {
                
                // Step 1: Load ALL history into cache first
                for (const msgData of history) {
                    const message = {
                        message: msgData.message,
                        sender: msgData.sender,
                        timestamp: msgData.timestamp
                    };
                    // Rebuild client cache from server data
                    sessions[sessionId].messages.push(message);
                }
                
                // Step 2: Then display cache
                for (const message of sessions[sessionId].messages) {
                    addMessageToSession(sessionId, message);
                }
            }
        } else {
            console.warn(`Failed to load history for session ${sessionId}:`, response.status);
        }
    } catch (error) {
        console.error(`Error loading history for session ${sessionId}:`, error);
    }
}

// Create a new session when recovery fails
async function createNewSession() {
    try {
        
        // Create temporary session ID for immediate UI feedback
        const tempSessionId = Date.now();
        
        // Create session object and UI elements immediately
        sessions[tempSessionId] = {
            id: tempSessionId,
            name: `Init...`,
            existsOnServer: false  // New session, not created on server yet
        };
        
        activeSessionId = tempSessionId;
        
        // Session persistence now handled server-side via cookies
        
        // Create tab and chat session elements immediately
        createTabElement(tempSessionId);
        createChatSessionElement(tempSessionId);
        switchToTab(tempSessionId);
        
        // Now request server to create the actual session
        const response = await fetch(apiUrl('/web/sessions/new'), {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            credentials: 'same-origin'
        });
        
        if (response.ok) {
            const data = await response.json();
            const newSessionId = parseInt(data.session_id);
            
            // Update the session with the real server-provided ID
            delete sessions[tempSessionId];
            sessions[newSessionId] = {
                id: newSessionId,
                name: `Init...`
            };
            
            // Update DOM elements with real session ID
            const tab = document.querySelector(`[data-session="${tempSessionId}"].tab`);
            const chatSession = document.querySelector(`[data-session="${tempSessionId}"].chat-session`);
            
            if (tab) {
                tab.setAttribute('data-session', newSessionId);
                // Update the close button event listener
                const closeButton = tab.querySelector('.tab-close');
                closeButton.onclick = null; // Remove old listener
                closeButton.addEventListener('click', (event) => {
                    event.stopPropagation();
                    closeTab(newSessionId);
                });
            }
            if (chatSession) {
                chatSession.setAttribute('data-session', newSessionId);
                const chatArea = chatSession.querySelector('.chat-area');
                if (chatArea) {
                    chatArea.id = `chat-area-${newSessionId}`;
                }
            }
            
            activeSessionId = newSessionId;
            nextSessionId = newSessionId + 1;
            
            // Mark session as existing on server
            sessions[newSessionId].existsOnServer = true;
            
            // Update session UI to show it's ready
            connectSession(newSessionId);
            
        } else {
            // Remove the temporary session on failure
            delete sessions[tempSessionId];
            const tab = document.querySelector(`[data-session="${tempSessionId}"].tab`);
            const chatSession = document.querySelector(`[data-session="${tempSessionId}"].chat-session`);
            if (tab) tab.remove();
            if (chatSession) chatSession.remove();
            
            showNotification('Failed to create new session', 'error');
        }
    } catch (error) {
        console.error('Error creating new session:', error);
        showNotification('Error creating new session', 'error');
    }
}

// Using simple HTTP request/response pattern
function loadTasksAfterRecovery() {
    // Load tasks when first session connects
    if (Object.keys(sessions).length > 0) {
        setTimeout(loadTasks, 1000);
    }
}

// Add message to specific session
function addMessageToSession(sessionId, message) {
    const chatArea = document.getElementById(`chat-area-${sessionId}`);
    if (!chatArea) return;
    
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${message.sender}-message`;
    
    const messageText = document.createElement('div');
    messageText.className = 'message-content';
    
    // Format message with proper line breaks and structure
    let formattedMessage = message.message;
    
    // Escape HTML entities to prevent tags from being interpreted
    formattedMessage = formattedMessage
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
    
    // Convert newlines to HTML line breaks for better formatting
    formattedMessage = formattedMessage.replace(/\n/g, '<br>');
    
    // Tool command formatting removed
    
    messageText.innerHTML = formattedMessage;
    
    const timestamp = document.createElement('div');
    timestamp.className = 'timestamp';
    timestamp.textContent = new Date(message.timestamp).toLocaleTimeString();
    
    messageDiv.appendChild(messageText);
    messageDiv.appendChild(timestamp);
    chatArea.appendChild(messageDiv);
    
    chatArea.scrollTop = chatArea.scrollHeight;
}

// Add typing indicator message
function addTypingMessage(sessionId, message) {
    const chatArea = document.getElementById(`chat-area-${sessionId}`);
    if (!chatArea) return null;
    
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${message.sender}-message typing`;
    
    const messageText = document.createElement('div');
    messageText.className = 'message-content typing-cursor';
    messageText.innerHTML = ''; // Empty content with typing cursor
    
    const timestamp = document.createElement('div');
    timestamp.className = 'timestamp';
    timestamp.textContent = new Date(message.timestamp).toLocaleTimeString();
    
    messageDiv.appendChild(messageText);
    messageDiv.appendChild(timestamp);
    chatArea.appendChild(messageDiv);
    
    chatArea.scrollTop = chatArea.scrollHeight;
    
    return messageDiv;
}

// Send chat message
async function sendMessage() {
    const message = messageInput.value.trim();
    
    if (message && activeSessionId) {
        try {
            // Step 1: Write to client cache FIRST (sequential data flow)
            const userMessage = {
                message: message,
                sender: 'user',
                timestamp: new Date().toISOString()
            };
            if (!sessions[activeSessionId].messages) {
                sessions[activeSessionId].messages = [];
            }
            sessions[activeSessionId].messages.push(userMessage);
            
            // Step 2: Display user message (after cache)
            addMessageToSession(activeSessionId, userMessage);
            
            messageInput.value = '';  // Clear input immediately
            messageInput.disabled = true;
            sendButton.disabled = true;
            
            // Show typing indicator
            const typingMessage = {
                message: '',
                sender: 'assistant',
                timestamp: new Date().toISOString()
            };
            const typingMessageDiv = addTypingMessage(activeSessionId, typingMessage);
            
            // Send message via HTTP and get AI response
            const response = await fetch(apiUrl('/web/chat'), {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                credentials: 'same-origin',
                body: JSON.stringify({ 
                    message: message,
                    session_id: activeSessionId
                })
            });
            
            if (response.ok) {
                // Message sent successfully - AI response will come via SSE
                console.log('Message sent successfully, waiting for SSE response...');
                // Keep typing indicator active until SSE delivers assistant response
            } else {
                const error = await response.text();
                // Remove typing indicator on error
                typingMessageDiv.remove();
                showNotification(`Failed to send message: ${error}`, 'error');
            }
            
        } catch (error) {
            // Remove typing indicator on error
            typingMessageDiv.remove();
            showNotification(`Failed to send message: ${error}`, 'error');
        } finally {
            messageInput.disabled = false;
            sendButton.disabled = false;
            messageInput.focus();
        }
    } else {
        showNotification('No active session', 'error');
    }
}

// Tab management
// Helper function to create tab element
function createTabElement(sessionId) {
    // Show "Init..." while loading, will be updated when connection is ready
    const sessionName = `Init...`;
    const tab = document.createElement('div');
    tab.className = 'tab';
    tab.setAttribute('data-session', sessionId);
    tab.innerHTML = `
        <span class="tab-title">${sessionName}</span>
        <button class="tab-close">X</button>
    `;
    
    // Add event listeners
    tab.addEventListener('click', () => switchToTab(sessionId));
    
    const closeButton = tab.querySelector('.tab-close');
    closeButton.addEventListener('click', (event) => {
        event.stopPropagation();
        closeTab(sessionId);
    });
    
    // Insert before the + button
    const addButton = document.getElementById('add-tab');
    tabList.insertBefore(tab, addButton);
}

// Helper function to create chat session element
function createChatSessionElement(sessionId) {
    const chatSession = document.createElement('div');
    chatSession.className = 'chat-session';
    chatSession.setAttribute('data-session', sessionId);
    chatSession.innerHTML = `
        <div class="chat-area" id="chat-area-${sessionId}">
            <!-- Messages will appear here -->
        </div>
    `;
    
    chatSessions.appendChild(chatSession);
}

async function addNewTab() {
    await createNewSession();
}

function switchToTab(sessionId) {
    // Update active session
    activeSessionId = sessionId;
    // Remember the active session for after page reload
    localStorage.setItem('lastActiveSession', sessionId);
    
    // Load history if not already loaded
    if (sessions[sessionId] && (!sessions[sessionId].messages || sessions[sessionId].messages.length === 0)) {
        loadSessionHistory(sessionId);
    }
    
    // Refresh display to show active plan for this session
    loadSavedPlans();
    
    // Update tab appearances
    document.querySelectorAll('.tab').forEach(tab => {
        tab.classList.remove('active');
    });
    document.querySelector(`[data-session="${sessionId}"].tab`).classList.add('active');
    
    // Update chat session visibility
    document.querySelectorAll('.chat-session').forEach(session => {
        session.classList.remove('active');
    });
    document.querySelector(`[data-session="${sessionId}"].chat-session`).classList.add('active');
    
    // Update connection status for active session
    const activeSession = sessions[sessionId];
    if (activeSession) {
        messageInput.disabled = false;
        sendButton.disabled = false;
    } else {
        messageInput.disabled = true;
        sendButton.disabled = true;
    }
    
    // Session name no longer needed in scheduler header
    
    // Refresh tasks for the active session
    loadTasks();
    
    // Focus input
    messageInput.focus();
}

async function closeTab(sessionId) {
    // If closing the last tab, create a new one first
    if (Object.keys(sessions).length <= 1) {
        await createNewSession();
        // Continue with closing the original tab
    }
    
    // Close SSE connection if this is the active session
    if (sessionId === activeSessionId && taskEventSource) {
        taskEventSource.close();
        taskEventSource = null;
    }
    
    // Delete session on server side
    try {
        const response = await fetch(apiUrl(`/web/sessions/${sessionId}`), {
            method: 'DELETE'
        });
        if (response.ok) {
            // Session deleted successfully
        } else {
            console.error(`Failed to delete session ${sessionId}:`, response.status);
        }
    } catch (error) {
        console.error('Error deleting session on server:', error);
    }
    
    // Remove from sessions
    delete sessions[sessionId];
    
    // Remove tab element
    const tab = document.querySelector(`[data-session="${sessionId}"].tab`);
    if (tab) tab.remove();
    
    // Remove chat session element
    const chatSession = document.querySelector(`[data-session="${sessionId}"].chat-session`);
    if (chatSession) chatSession.remove();
    
    // If closing active tab, switch to first available tab
    if (activeSessionId === sessionId) {
        const firstSessionId = Object.keys(sessions)[0];
        switchToTab(parseInt(firstSessionId));
    }
}

// Schedule new task for active session
async function scheduleTask() {
    const time = scheduleTimeInput.value.trim();
    const message = scheduleMessageInput.value.trim();
    
    if (!time || !message) {
        showNotification('Please enter both schedule and prompt', 'error');
        return;
    }
    
    try {
        const response = await fetch(apiUrl(`/web/sessions/${activeSessionId}/schedule`), {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                schedule_spec: time,
                message: message
            })
        });
        
        if (response.ok) {
            await response.json();
            showNotification(`Task scheduled for Session ${activeSessionId}!`, 'success');
            scheduleTimeInput.value = '';
            scheduleMessageInput.value = '';
            loadTasks(); // Refresh task list
        } else {
            const error = await response.json();
            showNotification('Error: ' + error.detail, 'error');
        }
    } catch (error) {
        showNotification('Error scheduling task: ' + error.message, 'error');
    }
}

// SSE connection for real-time tasks and messages
let taskEventSource = null;

// Load and display tasks for active session using SSE
let loadTasksTimeout;
function loadTasks() {
    // Debounce to prevent duplicate calls
    clearTimeout(loadTasksTimeout);
    loadTasksTimeout = setTimeout(() => {
        _loadTasksInternal();
    }, 100);
}

function _loadTasksInternal() {
    // Close existing SSE connection if any
    if (taskEventSource) {
        taskEventSource.close();
        taskEventSource = null;
    }
    
    if (!activeSessionId) {
        console.warn('No active session for tasks');
        return;
    }
    
    try {
        // Open SSE connection for real-time updates
        taskEventSource = new EventSource(apiUrl(`/web/sessions/${activeSessionId}/tasks`));
        
        taskEventSource.onmessage = function(event) {
            try {
                const data = JSON.parse(event.data);
                
                if (data.type === 'tasks') {
                    // Update tasks display in real-time
                    displayTasks(data.data);
                    taskCount.textContent = data.data.length;
                } else if (data.type === 'messages') {
                    // Display scheduled user messages and assistant responses from SSE
                    for (const message of data.data) {
                        // Display scheduled user messages (those starting with [AGENT])
                        if (message.sender === 'user' && message.message.startsWith('[AGENT]')) {
                            // Display the scheduled user message
                            addMessageToSession(activeSessionId, message);
                            
                            // Create typing indicator for assistant response (same as manual messages)
                            const typingMessage = {
                                message: '',
                                sender: 'assistant',
                                timestamp: new Date().toISOString()
                            };
                            const typingMessageDiv = addTypingMessage(activeSessionId, typingMessage);
                        }
                        // Display assistant responses via SSE
                        else if (message.sender === 'assistant') {
                            // Step 1: Write to client cache FIRST (sequential data flow)
                            if (!sessions[activeSessionId].messages) {
                                sessions[activeSessionId].messages = [];
                            }
                            sessions[activeSessionId].messages.push(message);
                            
                            // Step 2: Check if there's a typing indicator to replace
                            const typingIndicators = document.querySelectorAll(`#chat-area-${activeSessionId} .message.typing`);
                            const latestTypingIndicator = typingIndicators[typingIndicators.length - 1];
                            
                            if (latestTypingIndicator) {
                                // Replace typing indicator with new message
                                const messageContent = latestTypingIndicator.querySelector('.message-content');
                                if (messageContent) {
                                    // Format and display the response
                                    let formattedResponse = message.message
                                        .replace(/&/g, '&amp;')
                                        .replace(/</g, '&lt;')
                                        .replace(/>/g, '&gt;')
                                        .replace(/"/g, '&quot;')
                                        .replace(/'/g, '&#039;')
                                        .replace(/\n/g, '<br>');
                                    
                                    messageContent.innerHTML = formattedResponse;
                                    messageContent.classList.remove('typing-cursor');
                                    
                                    // Update timestamp
                                    const timestampElement = latestTypingIndicator.querySelector('.timestamp');
                                    if (timestampElement) {
                                        timestampElement.textContent = new Date().toLocaleTimeString();
                                    }
                                    
                                    // Remove typing class
                                    latestTypingIndicator.classList.remove('typing');
                                }
                            } else {
                                // No typing indicator, just add as new message
                                addMessageToSession(activeSessionId, message);
                            }
                        }
                        // Ignore all other message types (user, system, etc.)
                    }
                }
            } catch (parseError) {
                console.error('Error parsing SSE data:', parseError);
            }
        };
        
        taskEventSource.onerror = function(error) {
            console.error('SSE error:', error);
            // Don't show notification for every error to avoid spam
        };
        
        taskEventSource.onopen = function() {
            console.log('SSE connection opened for session', activeSessionId);
        };
        
    } catch (error) {
        console.error('Error setting up SSE for tasks:', error);
        showNotification('Error loading tasks', 'error');
    }
}

// Display tasks in the sidebar
function displayTasks(tasks) {
    if (tasks.length === 0) {
        tasksList.innerHTML = '<div class="no-tasks">No scheduled tasks</div>';
        return;
    }
    
    tasksList.innerHTML = '';
    
    tasks.forEach((task, index) => {
        const taskDiv = document.createElement('div');
        taskDiv.className = `task-item ${task.is_running ? 'task-running' : ''}`;
        
        const nextRun = new Date(task.next_run);
        const lastRun = task.last_run ? new Date(task.last_run) : null;
        
        taskDiv.innerHTML = `
            <div class="task-schedule">
                ${task.schedule_spec}
                <span class="task-delete" onclick="deleteTask(${index})" title="Delete task" style="float: right; cursor: pointer;">🗑</span>
            </div>
            <div class="task-message">${task.message}</div>
            <div class="task-next-run">
                ${lastRun ? `Last: ${lastRun.toLocaleString()}<br>` : ''}Next: ${nextRun.toLocaleString()}
            </div>
        `;
        
        tasksList.appendChild(taskDiv);
    });
}

// Delete individual task
async function deleteTask(index) {
    if (!confirm('Delete this scheduled task?')) {
        return;
    }
    
    try {
        const response = await fetch(apiUrl(`/web/sessions/${activeSessionId}/tasks/${index}`), {
            method: 'DELETE'
        });
        
        if (response.ok) {
            const data = await response.json();
            showNotification(`Task deleted: ${data.message}`, 'success');
            loadTasks(); // Refresh the task list
        } else {
            const errorData = await response.json();
            showNotification(`Error deleting task: ${errorData.detail}`, 'error');
        }
    } catch (error) {
        showNotification('Error deleting task', 'error');
        console.error('Delete task error:', error);
    }
}

// Clear all tasks for active session
async function clearAllTasks() {
    if (!confirm(`Clear all scheduled tasks for Session ${activeSessionId}?`)) {
        return;
    }
    
    try {
        const response = await fetch(apiUrl(`/web/sessions/${activeSessionId}/tasks`), {
            method: 'DELETE'
        });
        
        if (response.ok) {
            const result = await response.json();
            showNotification(result.message, 'success');
            loadTasks(); // Refresh task list
        } else {
            showNotification('Error clearing tasks', 'error');
        }
    } catch (error) {
        showNotification('Error clearing tasks: ' + error.message, 'error');
    }
}

// Show notification
function showNotification(message, type = 'info') {
    const notification = document.createElement('div');
    notification.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        padding: 12px 20px;
        border-radius: 5px;
        color: white;
        font-weight: bold;
        z-index: 1000;
        max-width: 300px;
        word-wrap: break-word;
    `;
    
    switch (type) {
        case 'success':
            notification.style.backgroundColor = '#28a745';
            break;
        case 'error':
            notification.style.backgroundColor = '#dc3545';
            break;
        case 'warning':
            notification.style.backgroundColor = '#ffc107';
            notification.style.color = '#000';
            break;
        default:
            notification.style.backgroundColor = '#17a2b8';
    }
    
    notification.textContent = message;
    document.body.appendChild(notification);
    
    setTimeout(() => {
        if (notification.parentNode) {
            notification.parentNode.removeChild(notification);
        }
    }, 4000);
}

// Plan management functions
async function savePlan() {
    try {
        // Get the current active plan name to use as default
        const activeResponse = await fetch(apiUrl(`/web/sessions/${activeSessionId}/active-plan`));
        let defaultName = '';
        
        if (activeResponse.ok) {
            const activeData = await activeResponse.json();
            defaultName = activeData.active_plan || '';
        }
        
        const planName = prompt('Enter a name for this task plan:', defaultName);
        
        // If user cancelled the prompt or entered empty name, don't save
        if (planName === null || planName.trim() === '') {
            return;
        }
        
        const url = apiUrl(`/web/task-plans/save?plan_name=${encodeURIComponent(planName.trim())}&session_id=${activeSessionId}`);
        const response = await fetch(url, {
            method: 'POST'
        });
        
        if (response.ok) {
            const data = await response.json();
            showNotification(`${data.message}`, 'success');
            await loadSavedPlans(); // Refresh the plans list
        } else {
            const errorData = await response.json();
            showNotification(`Error saving plan: ${errorData.detail}`, 'error');
        }
    } catch (error) {
        showNotification('Error saving task plan', 'error');
        console.error('Save plan error:', error);
    }
}

async function refreshPlans() {
    try {
        const response = await fetch(apiUrl('/web/task-plans'));
        if (response.ok) {
            const data = await response.json();
            
            if (data.plans.length === 0) {
                showNotification('No saved plans found', 'info');
            } else if (data.plans.length === 1) {
                // If only 1 plan, just show notification
                showNotification(`Found ${data.plans.length} saved plan`, 'info');
            } else {
                // If 2 or more plans, show selection interface
                showPlanSelectionModal(data.plans);
            }
            
            // Always update the display
            displayCurrentPlans(data.plans);
        } else {
            showNotification('Error loading saved plans', 'error');
        }
    } catch (error) {
        showNotification('Error loading saved plans', 'error');
        console.error('Load plans error:', error);
    }
}

let loadPlansTimeout;
async function loadSavedPlans() {
    // Debounce to prevent duplicate calls
    clearTimeout(loadPlansTimeout);
    loadPlansTimeout = setTimeout(() => {
        _loadSavedPlansInternal();
    }, 100);
}

async function _loadSavedPlansInternal() {
    try {
        const response = await fetch(apiUrl('/web/task-plans'));
        if (response.ok) {
            const data = await response.json();
            displayCurrentPlans(data.plans);
        } else {
            showNotification('Error loading saved plans', 'error');
        }
    } catch (error) {
        showNotification('Error loading saved plans', 'error');
        console.error('Load plans error:', error);
    }
}

let displayPlansTimeout;
async function displayCurrentPlans(plans) {
    // Debounce to prevent duplicate calls
    clearTimeout(displayPlansTimeout);
    displayPlansTimeout = setTimeout(() => {
        _displayCurrentPlansInternal(plans);
    }, 100);
}

async function _displayCurrentPlansInternal(plans) {
    const currentPlansList = document.getElementById('current-plans-list');
    
    try {
        // Get active plan for current session from server
        const response = await fetch(apiUrl(`/web/sessions/${activeSessionId}/active-plan`));
        if (!response.ok) {
            currentPlansList.innerHTML = '<div class="no-plans">No active plan</div>';
            return;
        }
        
        const data = await response.json();
        const activePlanName = data.active_plan;
        
        if (!activePlanName) {
            currentPlansList.innerHTML = '<div class="no-plans">No active plan</div>';
            return;
        }
        
        // Find the active plan from the plans list
        const activePlan = plans.find(plan => plan.name === activePlanName);
        
        if (!activePlan) {
            currentPlansList.innerHTML = '<div class="no-plans">Active plan not found</div>';
            return;
        }
        
        currentPlansList.innerHTML = `
            <div class="plan-item active-plan">
                <div class="plan-item-name">${activePlan.name}</div>
                <div class="plan-item-meta">${activePlan.task_count} tasks, used by ${activePlan.session_count} session${activePlan.session_count !== 1 ? 's' : ''}</div>
            </div>
        `;
    } catch (error) {
        console.error('Error getting active plan:', error);
        currentPlansList.innerHTML = '<div class="no-plans">No active plan</div>';
    }
}

function showPlanSelectionModal(plans) {
    // Create modal HTML
    const modalHtml = `
        <div id="plan-selection-modal" class="modal-overlay">
            <div class="modal-content">
                <div class="modal-header">
                    <h3>Select a Plan to Load</h3>
                    <button class="modal-close" onclick="closePlanSelectionModal()">&times;</button>
                </div>
                <div class="modal-body">
                    <div class="plan-selection-list">
                        ${plans.map(plan => `
                            <div class="selectable-plan-item" onclick="loadSelectedPlan('${plan.name}')">
                                <div class="plan-item-name">${plan.name}</div>
                                <div class="plan-item-meta">${plan.task_count} tasks, used by ${plan.session_count} session${plan.session_count !== 1 ? 's' : ''}</div>
                            </div>
                        `).join('')}
                    </div>
                </div>
            </div>
        </div>
    `;
    
    // Add modal to page
    document.body.insertAdjacentHTML('beforeend', modalHtml);
}

function closePlanSelectionModal() {
    const modal = document.getElementById('plan-selection-modal');
    if (modal) {
        modal.remove();
    }
}

async function loadSelectedPlan(planName) {
    closePlanSelectionModal();
    
    if (!confirm(`Load task plan "${planName}"? This will replace all current tasks.`)) {
        return;
    }
    
    try {
        // First verify the session exists on the backend with retries
        let sessionReady = false;
        for (let i = 0; i < 3; i++) {
            const sessionCheck = await fetch(apiUrl(`/web/sessions/${activeSessionId}`));
            if (sessionCheck.ok) {
                sessionReady = true;
                break;
            }
            if (i < 2) { // Wait before retry, except on last attempt
                await new Promise(resolve => setTimeout(resolve, 1000));
            }
        }
        
        if (!sessionReady) {
            showNotification('Session not ready yet, please wait a moment and try again', 'warning');
            return;
        }
        
        const response = await fetch(apiUrl(`/web/task-plans/${encodeURIComponent(planName)}/load?session_id=${activeSessionId}`), {
            method: 'POST'
        });
        
        if (response.ok) {
            const data = await response.json();
            showNotification(`${data.message}`, 'success');
            
            // Add a small delay to ensure backend has processed the plan loading
            setTimeout(async () => {
                await loadTasks(); // Refresh the task list
            }, 500);
            
            await loadSavedPlans(); // Refresh the display to show active plan
        } else {
            const errorData = await response.json();
            showNotification(`Error loading plan: ${errorData.detail}`, 'error');
        }
    } catch (error) {
        showNotification('Error loading task plan', 'error');
        console.error('Load plan error:', error);
    }
}



// Event listeners
sendButton.addEventListener('click', sendMessage);
scheduleButton.addEventListener('click', scheduleTask);
clearAllTasksButton.addEventListener('click', clearAllTasks);

// Plan management event listeners
const savePlanButton = document.getElementById('save-plan-button');
const loadPlansButton = document.getElementById('load-plans-button');
savePlanButton.addEventListener('click', savePlan);
loadPlansButton.addEventListener('click', refreshPlans);

messageInput.addEventListener('keypress', function(e) {
    if (e.key === 'Enter') {
        sendMessage();
    }
});

scheduleTimeInput.addEventListener('keypress', function(e) {
    if (e.key === 'Enter') {
        scheduleMessageInput.focus();
    }
});

scheduleMessageInput.addEventListener('keypress', function(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        scheduleTask();
    }
});

// Tab click handlers
document.addEventListener('click', function(e) {
    if (e.target.classList.contains('tab') || e.target.parentElement.classList.contains('tab')) {
        const tab = e.target.classList.contains('tab') ? e.target : e.target.parentElement;
        const sessionId = parseInt(tab.getAttribute('data-session'));
        if (sessionId) {
            switchToTab(sessionId);
        }
    }
});

// SSE replaces polling - no need for setInterval

// Initialize page: attempt recovery then connect sessions
async function initializePage() {
    // Clear old localStorage session data (now using server-side persistence)
    localStorage.removeItem('agentSessions');
    localStorage.removeItem('activeSessionId');
    
    // Clear everything first
    sessions = {};
    activeSessionId = null;
    nextSessionId = null;
    
    // Clear any existing tabs and sessions from DOM
    const allTabs = document.querySelectorAll('.tab');
    allTabs.forEach(tab => tab.remove());
    
    const allChatSessions = document.querySelectorAll('.chat-session');
    allChatSessions.forEach(session => session.remove());
    
    // Try to recover existing sessions
    await attemptSessionRecovery();
    
    // Load saved plans on page initialization
    await loadSavedPlans();
    
    // If no sessions were recovered, create a new one
    if (Object.keys(sessions).length === 0) {
        await createNewSession();
    } else {
        // Connect to recovered sessions
        loadTasksAfterRecovery();
    }
}

// Initialize on page load
initializePage();


