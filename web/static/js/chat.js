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

// Sessions will be initialized in initializePage() to prevent early WebSocket connections

// WebSocket connection for specific session
function connectSession(sessionId) {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/${sessionId}`;
    
    const websocket = new WebSocket(wsUrl);
    
    websocket.onopen = function(event) {
        console.log(`Session ${sessionId} connected`);
        sessions[sessionId].websocket = websocket;
        
        // Update tab and session name to show ready state
        const last4Digits = sessionId.toString().slice(-4);
        const sessionName = `Chat ${last4Digits}`;
        
        const tab = document.querySelector(`[data-session="${sessionId}"].tab .tab-title`);
        if (tab && tab.textContent.includes('Init...')) {
            tab.textContent = sessionName;
        }
        
        // Update session object name
        if (sessions[sessionId]) {
            sessions[sessionId].name = sessionName;
        }
        
        // Update UI if this is the active session
        if (sessionId === activeSessionId) {
            messageInput.disabled = false;
            sendButton.disabled = false;
        }
    };
    
    websocket.onmessage = function(event) {
        const message = JSON.parse(event.data);
        
        // Add message to this specific session
        addMessageToSession(sessionId, message);
        
        // Store message in session history
        sessions[sessionId].messages.push(message);
    };
    
    websocket.onclose = function(event) {
        console.log(`Session ${sessionId} disconnected (code: ${event.code})`);
        sessions[sessionId].websocket = null;
        
        // Update UI if this is the active session
        if (sessionId === activeSessionId) {
            messageInput.disabled = true;
            sendButton.disabled = true;
        }
        
        // If session was rejected (code 4004 or 1006 from 403), don't try to reconnect
        if (event.code === 4004 || event.code === 1006) {
            console.log(`Session ${sessionId} rejected by server - removing from client`);
            // Remove this session from client
            delete sessions[sessionId];
            
            // Remove tab and chat session elements
            const tab = document.querySelector(`[data-session="${sessionId}"].tab`);
            const chatSession = document.querySelector(`[data-session="${sessionId}"].chat-session`);
            if (tab) tab.remove();
            if (chatSession) chatSession.remove();
            
            // If this was the active session and no sessions remain, create a new one
            if (sessionId === activeSessionId && Object.keys(sessions).length === 0) {
                createNewSession();
            }
        } else {
            // Normal disconnect - try to reconnect after 3 seconds
            setTimeout(() => connectSession(sessionId), 3000);
        }
    };
    
    websocket.onerror = function(error) {
        console.error(`WebSocket error for session ${sessionId}:`, error);
    };
    
    return websocket;
}

// Attempt session recovery from server
async function attemptSessionRecovery() {
    if (sessionRecoveryAttempted) return;
    sessionRecoveryAttempted = true;
    
    try {
        const response = await fetch('/api/sessions');
        const data = await response.json();
        if (data.sessions && data.sessions.length > 0) {
            
            // Check if any sessions have meaningful data before clearing current state
            let meaningfulSessions = [];
            
            for (const sessionInfo of data.sessions) {
                const hasHistory = sessionInfo.history_count > 0;
                const hasTasks = sessionInfo.task_count > 0;
                
                if (hasHistory || hasTasks) {
                    meaningfulSessions.push(sessionInfo);
                }
            }
            
            // Only proceed with recovery if we found meaningful sessions
            if (meaningfulSessions.length > 0) {
                
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
                        messages: [],
                        websocket: null
                    };
                    
                    // Create tab for this session
                    createTabElement(sessionId);
                    createChatSessionElement(sessionId);
                    
                    if (!hasActiveSessions) {
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

// Create a new session when recovery fails
async function createNewSession() {
    try {
        // Create temporary session ID for immediate UI feedback
        const tempSessionId = Date.now();
        
        // Create session object and UI elements immediately
        sessions[tempSessionId] = {
            id: tempSessionId,
            name: `Init...`,
            messages: [],
            websocket: null
        };
        
        activeSessionId = tempSessionId;
        
        // Create tab and chat session elements immediately
        createTabElement(tempSessionId);
        createChatSessionElement(tempSessionId);
        switchToTab(tempSessionId);
        
        // Now request server to create the actual session
        const response = await fetch('/api/sessions/new', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            }
        });
        
        if (response.ok) {
            const data = await response.json();
            const newSessionId = parseInt(data.session_id);
            
            // Update the session with the real server-provided ID
            delete sessions[tempSessionId];
            sessions[newSessionId] = {
                id: newSessionId,
                name: `Init...`,
                messages: [],
                websocket: null
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
            
            // Connect to the new session
            connectSession(newSessionId);
            
            console.log(`Created new session ${newSessionId}`);
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

// Connect all sessions
function connectAllSessions() {
    for (const sessionId in sessions) {
        if (!sessions[sessionId].websocket || sessions[sessionId].websocket.readyState === WebSocket.CLOSED) {
            connectSession(parseInt(sessionId));
        }
    }
    
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

// Send chat message
function sendMessage() {
    const message = messageInput.value.trim();
    const activeSession = sessions[activeSessionId];
    
    if (message && activeSession && activeSession.websocket && activeSession.websocket.readyState === WebSocket.OPEN) {
        activeSession.websocket.send(JSON.stringify({type: "chat", message: message}));
        messageInput.value = '';
    } else {
        showNotification('Not connected to chat session', 'error');
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
    if (activeSession && activeSession.websocket && activeSession.websocket.readyState === WebSocket.OPEN) {
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
    console.log(`closeTab called for session ${sessionId}`);
    
    // If closing the last tab, create a new one first
    if (Object.keys(sessions).length <= 1) {
        await createNewSession();
        // Continue with closing the original tab
    }
    
    // Close WebSocket connection
    const session = sessions[sessionId];
    if (session && session.websocket) {
        session.websocket.close();
    }
    
    // Delete session on server side
    try {
        const response = await fetch(`/api/sessions/${sessionId}`, {
            method: 'DELETE'
        });
        if (response.ok) {
            console.log(`Successfully deleted session ${sessionId} on server`);
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
        const response = await fetch(`/api/sessions/${activeSessionId}/schedule`, {
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
            const result = await response.json();
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

// Load and display tasks for active session
async function loadTasks() {
    try {
        const response = await fetch(`/api/sessions/${activeSessionId}/tasks`);
        const data = await response.json();
        
        displayTasks(data.tasks);
        taskCount.textContent = data.tasks.length;
        
    } catch (error) {
        console.error('Error loading tasks:', error);
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
            <div class="task-schedule">${task.schedule_spec}</div>
            <div class="task-message">${task.message}</div>
            <div class="task-next-run">
                Next: ${nextRun.toLocaleString()}
                ${lastRun ? `<br>Last: ${lastRun.toLocaleString()}` : ''}
            </div>
            <div class="task-actions">
                <button class="delete-task" onclick="deleteTask(${index})">Delete</button>
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
        showNotification('Individual task deletion not yet implemented. Use "Clear All" for now.', 'warning');
    } catch (error) {
        showNotification('Error deleting task', 'error');
    }
}

// Clear all tasks for active session
async function clearAllTasks() {
    if (!confirm(`Clear all scheduled tasks for Session ${activeSessionId}?`)) {
        return;
    }
    
    try {
        const response = await fetch(`/api/sessions/${activeSessionId}/tasks`, {
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

// Event listeners
sendButton.addEventListener('click', sendMessage);
scheduleButton.addEventListener('click', scheduleTask);
clearAllTasksButton.addEventListener('click', clearAllTasks);

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

// Auto-refresh tasks every 30 seconds
setInterval(loadTasks, 30000);

// Initialize page: attempt recovery then connect sessions
async function initializePage() {
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
    
    // If no sessions were recovered, create a new one
    if (Object.keys(sessions).length === 0) {
        await createNewSession();
    } else {
        // Connect to recovered sessions
        connectAllSessions();
    }
}

// Initialize on page load
initializePage();