"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""
from fasthtml.common import *
from dataclasses import dataclass
from datetime import datetime
import random
import ast
import json
from src.open_apps.apps.start_page.helper import create_logo_header


@dataclass
class Messages:
    user: str
    messages: list[str]
    senders: list[str]
    timestamps: list[str]

logo_title_container = None

# Set up the app, including daisyui and tailwind for the chat component
_base_chat_script = (
    Script("""
    function scrollToBottom() {
        const container = document.querySelector('#chat-container');
        if (container) {
            // Add smooth scrolling behavior
            container.scrollTo({
                top: container.scrollHeight,
                behavior: 'smooth'
            });
        }
    }
    
    // Scroll to a specific message
    function scrollToMessage(messageId) {
        console.log("Scrolling to message:", messageId);  // Debug log
        const message = document.getElementById(messageId);
        if (message) {
            // Make all messages visible before scrolling
            document.querySelectorAll('.chat').forEach(el => {
                el.style.display = '';
            });
            
            // Clear search highlight from all messages
            document.querySelectorAll('.search-highlight').forEach(el => {
                el.classList.remove('search-highlight');
            });
            
            // Add highlight to this message only
            message.classList.add('search-highlight');
            
            // Scroll to the message
            message.scrollIntoView({ behavior: 'smooth', block: 'center' });
            
            // Flash effect to highlight the message
            message.classList.add('bg-accent-focus', 'bg-opacity-20');
            setTimeout(() => {
                message.classList.remove('bg-accent-focus', 'bg-opacity-20');
            }, 1500);
        } else {
            console.error("Message not found:", messageId);  // Debug log
        }
    }
    
    // Initial load scroll
    document.addEventListener('DOMContentLoaded', scrollToBottom);
    
    // Watch for DOM changes in the chat container
    const observer = new MutationObserver(function(mutations) {
        scrollToBottom();
    });
    
    document.addEventListener('DOMContentLoaded', function() {
        console.log("DOM loaded");  // Debug log
        
        const chatContainer = document.querySelector('#chat-container');
        if (chatContainer) {
            observer.observe(chatContainer, {
                childList: true,
                subtree: true
            });
        }
        
        // Set up search toggle functionality
        const searchToggle = document.getElementById('search-toggle');
        const searchBar = document.getElementById('search-bar');
        
        if (searchToggle && searchBar) {
            searchToggle.addEventListener('click', function() {
                searchBar.classList.toggle('hidden');
                if (!searchBar.classList.contains('hidden')) {
                    document.getElementById('search-input').focus();
                } else {
                    // Clear search when hiding the search bar
                    clearSearch();
                }
            });
        }
        
        // Add global function for search result clicks
        window.handleSearchResultClick = function(messageId) {
            console.log("Search result clicked:", messageId);  // Debug log
            scrollToMessage(messageId);
        };
    });
    
    // HTMX specific handlers
    document.body.addEventListener('htmx:beforeSwap', function(evt) {
        if (evt.detail.target.id === 'chatlist') {
            evt.detail.shouldSwap = true;
            evt.detail.isSettled = true;
        }
    });
    
    document.body.addEventListener('htmx:afterSwap', function(evt) {
        if (evt.detail.target.id === 'chatlist') {
            scrollToBottom();
        }
    });
    
    // Search functionality
    function searchMessages() {
        const searchTerm = document.getElementById('search-input').value.toLowerCase();
        if (searchTerm === '') {
            clearSearch();
            return;
        }
        
        const chatMessages = document.querySelectorAll('.chat');
        let matchCount = 0;
        let searchResults = [];
        
        // First clear all message IDs and highlights
        chatMessages.forEach((message, index) => {
            message.id = `msg-${index}`;  // Assign IDs to all messages for later reference
            message.classList.remove('search-highlight');
        });
        
        chatMessages.forEach((chatMessage, index) => {
            const bubble = chatMessage.querySelector('.chat-bubble');
            const messageText = bubble.textContent.toLowerCase();
            const isUser = chatMessage.classList.contains('chat-end');
            
            // Get the sender name from the chat-header
            const headerText = chatMessage.querySelector('.chat-header').textContent;
            const sender = isUser ? 'You' : headerText.trim();
            
            // Include the timestamp in search results (now with date)
            const timestamp = chatMessage.querySelector('.chat-footer').textContent;
            
            if (messageText.includes(searchTerm)) {
                // Show and highlight matching messages
                chatMessage.style.display = '';
                chatMessage.classList.add('search-highlight');
                
                // Add to search results
                matchCount++;
                
                // Create preview text with highlighting
                let previewText = messageText;
                if (previewText.length > 40) {
                    // Find position of search term
                    const pos = previewText.indexOf(searchTerm);
                    // Create snippet with context around the match
                    const start = Math.max(0, pos - 15);
                    const end = Math.min(previewText.length, pos + searchTerm.length + 15);
                    previewText = (start > 0 ? '...' : '') + 
                                previewText.substring(start, end) + 
                                (end < previewText.length ? '...' : '');
                }
                
                // Add to search results array
                searchResults.push({
                    messageId: `msg-${index}`,
                    sender: sender,
                    preview: previewText,
                    timestamp: timestamp,
                    text: messageText
                });
            } else {
                // Hide non-matching messages
                chatMessage.style.display = 'none';
            }
        });
        
        // The rest of the function remains the same
        document.getElementById('search-results').textContent = 
            matchCount > 0 ? `${matchCount} result${matchCount !== 1 ? 's' : ''} for "${searchTerm}"` : `No results for "${searchTerm}"`;
        
        const searchResultsContainer = document.getElementById('search-results-container');
        searchResultsContainer.innerHTML = '';
        
        if (searchResults.length > 0) {
            searchResults.forEach(result => {
                const resultItem = document.createElement('div');
                resultItem.className = 'search-result-item p-2 hover:bg-base-300 rounded cursor-pointer flex flex-col';
                resultItem.setAttribute('onclick', `window.handleSearchResultClick('${result.messageId}')`);
                
                const headerEl = document.createElement('div');
                headerEl.className = 'font-bold text-sm flex justify-between';
                
                const senderEl = document.createElement('span');
                senderEl.textContent = result.sender;
                
                const timeEl = document.createElement('span');
                timeEl.className = 'text-xs opacity-70';
                timeEl.textContent = result.timestamp;
                
                headerEl.appendChild(senderEl);
                headerEl.appendChild(timeEl);
                
                const previewEl = document.createElement('div');
                previewEl.className = 'text-sm';
                previewEl.textContent = result.preview;
                
                resultItem.appendChild(headerEl);
                resultItem.appendChild(previewEl);
                searchResultsContainer.appendChild(resultItem);
            });
        }
        
        if (searchTerm === '') {
            clearSearch();
        } else if (matchCount > 0) {
            const firstHighlight = document.querySelector('.search-highlight');
            if (firstHighlight) {
                firstHighlight.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }
        }
    }
    
    function clearSearch() {
        document.getElementById('search-input').value = '';
        
        // Clear search results
        document.getElementById('search-results').textContent = '';
        document.getElementById('search-results-container').innerHTML = '';
        
        // Restore all messages to their original state
        const chatMessages = document.querySelectorAll('.chat');
        chatMessages.forEach(chatMessage => {
            chatMessage.style.display = '';
            chatMessage.classList.remove('search-highlight');
        });
        
        scrollToBottom();
    }
    """)
)
_base_hdrs = (
    picolink,
    Script(src="https://unpkg.com/htmx.org@1.9.10"),  # Add this line if not present
    Script(src="https://cdn.tailwindcss.com"),
    Link(
        rel="stylesheet",
        href="https://cdn.jsdelivr.net/npm/daisyui@4.11.1/dist/full.min.css",
    ),
    Link(
        rel="stylesheet",
        href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css",
    ),
    _base_chat_script,
)
app = FastHTML(hdrs=_base_hdrs, cls="p-4 max-w-lg mx-auto")


def set_environment(config):
    """Set environment variables for the messenger app"""
    global app, logo_title_container, message_history_db, user_logo, group_logo
    # if getattr(config.messenger, 'no_css', False):
    #     app.hdrs = ()
    #     app.config = config
    #     db = database(app.config.messenger.database_path)
    #     message_history_db = db.create(Messages, pk="user")
    #     populate_database(config, message_history_db)
    #     user_logo_url, group_logo_url = app.config.start_page.apps.messages.user_icon, app.config.start_page.apps.messages.group_icon
    #     user_logo = Img(src=user_logo_url, cls="h-10 mr-3")
    #     group_logo = Img(src=group_logo_url, cls="h-10 mr-3")
    #     logo_title_container = create_logo_header(
    #         app_config=config.start_page.apps.codeeditor,
    #         base_url="/messages",
    #         current_file_path=__file__
    #     )
    #     return
    # Create styles with environment variables
    env_styles = Style(
        f"""
        :root {{
            --custom-font-size: {config.messenger.font_size}px;
            --custom-font-family: {config.messenger.font};
            --custom-font-color: {config.messenger.fontcolor};
            --custom-background-color: {config.messenger.background_color};
            --chat-font-size: {config.messenger.chat_font_size}px;
            --chat-font-family: {config.messenger.chat_font};
            --chat-font-color: {config.messenger.chat_fontcolor};
            --chat-header-font-color: {config.messenger.chat_header_fontcolor};
            --chat-primary-bubble-color: {config.messenger.chat_primary_bubble_color};
            --chat-secondary-bubble-color: {config.messenger.chat_secondary_bubble_color};
            --chat-display-background-color: {config.messenger.chat_display_background_color};
        }}
        body {{
            background-color: var(--custom-background-color);
        }}
        /* Global text styling */
        h1, h2, h3, p, div {{
            font-family: var(--custom-font-family) !important;
            color: var(--custom-font-color) !important;
        }}

        /* Message list specific styling */
        .text-2xl {{
            font-size: calc(var(--custom-font-size) * 1.5) !important;
        }}

        .text-sm {{
            font-size: calc(var(--custom-font-size) * 0.875) !important;
        }}
        
        /* Chat specific styling */
        .chat .chat-header {{
            font-family: var(--chat-font-family) !important;
            color: var(--chat-header-font-color) !important;
            margin-bottom: calc(var(--chat-font-size) * 0.5);
        }}
        
        .chat .chat-bubble {{
            font-family: var(--chat-font-family) !important;
            font-size: var(--chat-font-size) !important;
            color: var(--chat-font-color) !important;
            padding: calc(var(--chat-font-size) * 0.75) calc(var(--chat-font-size) * 1);
            min-height: calc(var(--chat-font-size) * 2);
            display: flex;
            align-items: center;
        }}
        .input {{
            font-family: var(--chat-font-family) !important;
            font-size: var(--chat-font-size) !important;
            color: var(--chat-font-color) !important;
        }}

        # Add these new styles for chat bubbles
        .chat-bubble-primary {{
            background-color: var(--chat-primary-bubble-color) !important;
        }}
        
        .chat-bubble-secondary {{
            background-color: var(--chat-secondary-bubble-color) !important;
        }}
    """
    )

    # Update app headers by extending existing ones
    # Note: in future implementations, we might need to preserve
    # the order of the style files, scripts and links to prevent conflicts
    app.hdrs = (*_base_hdrs, env_styles)
    app.config = config
    # create database
    db = database(app.config.messenger.database_path)
    message_history_db = db.create(Messages, pk="user")
    populate_database(config, message_history_db)
    user_logo_url, group_logo_url = app.config.start_page.apps.messages.user_icon, app.config.start_page.apps.messages.group_icon
    user_logo = Img(src=user_logo_url, cls="h-10 mr-3")
    group_logo = Img(src=group_logo_url, cls="h-10 mr-3")

    logo_title_container = create_logo_header(
        app_config=config.start_page.apps.messages,
        base_url="/messages",
        current_file_path=__file__
    )

def populate_database(config, db):
    """Adds chat history to database"""
    chat_history = config.messenger.chat_history
    for user in chat_history:
        print("adding ", user, " to db")
        user_chat = chat_history[user]
        messages = [m[0] for m in user_chat]
        senders = [m[2] for m in user_chat]
        timestamps = [m[3] for m in user_chat]
        messages = Messages(user=user, messages=messages, senders=senders, timestamps=timestamps)
        db.insert(messages)


def add_new_message_to_history(user, message, sender, timestamp):
    """Adds a new message to the database"""
    chat_history = message_history_db[user]
    messages = ast.literal_eval(chat_history.messages)
    messages.append(message)
    senders = ast.literal_eval(chat_history.senders)
    senders.append(sender)
    timestamps = ast.literal_eval(chat_history.timestamps)
    timestamps.append(timestamp)
    chat_history.messages = messages
    chat_history.senders = senders
    chat_history.timestamps = timestamps
    message_history_db.update(chat_history)

# Chat message component (renders a chat bubble)
def ChatMessage(message, sender, timestamp=None):
    if sender == "you":
        bubble_class = "chat-bubble-primary custom-primary-bubble bg-[var(--chat-primary-bubble-color)] text-[var(--chat-font-color)]"
        chat_class = "chat-end"
    else:
        bubble_class = "chat-bubble-secondary"
        chat_class = "chat-start"
    if timestamp is None:
        timestamp = datetime.now().strftime("%b %d, %I:%M %p")  # Format: Apr 16, 10:30 AM
    return Div(cls=f"chat {chat_class}")(
        Div(sender, cls="chat-header"),
        Div(message, cls=f"chat-bubble {bubble_class}"),
        Div(timestamp, cls="chat-footer opacity-70 text-xs"),
        Hidden(message, name="messages"),
    )

# The input field for the user message. Also used to clear the
# input field after sending a message via an OOB swap
def ChatInput():
    return Input(
        name="msg",
        id="msg-input",
        placeholder="Type a message",
        cls="input input-bordered w-full",
        hx_swap_oob="true",
    )

# Search bar component with results area
def SearchBar():
    return Div(
        id="search-bar",
        cls="hidden flex-col gap-2 p-2 bg-base-200 border-b animate-fade-in"
    )(
        Div(cls="flex items-center gap-2")(
            Input(
                id="search-input",
                placeholder="Search messages...",
                cls="input input-bordered w-full",
                onkeyup="searchMessages()"
            ),
            Button(
                I(cls="fas fa-times"),
                cls="btn btn-circle btn-sm", 
                onclick="clearSearch()",
                type="button"
            )
        ),
        Div(id="search-results", cls="text-sm text-info font-bold mt-1"),
        Div(
            id="search-results-container", 
            cls="flex flex-col gap-1 mt-1 max-h-40 overflow-y-auto"
        )
    )

# the main screen, create a page that displays a list of users. Each user can be clicked on to display the detailed messages
@app.get("/messages")
def index():
    chats = []
    for history in message_history_db():
        messages = ast.literal_eval(history.messages)
        timestamps = ast.literal_eval(history.timestamps)
        senders = ast.literal_eval(history.senders)
        if messages:
            last_sender = senders[-1] if senders else ""
            last_message = messages[-1]
            
            # Add prefix to the last message
            if last_sender == "you":
                message_preview = f"You: {last_message}"
            else:
                message_preview = f"{last_message}" if "group" not in history.user.lower() else f"{last_sender}: {last_message}"

            chats.append({
                "user": history.user,
                "last_message": message_preview,
                "last_timestamp": timestamps[-1] if timestamps else ""
            })
        else:
            chats.append({
                "user": history.user,
                "last_message": "No messages yet",
                "last_timestamp": ""
            })
    userlist = [
        A(
                # Avatar
                # Use logo from icons
                Div(
                    Div(
                        user_logo if 'group' not in chat['user'].lower() else group_logo,
                    ),
                # Chat info
                Div(
                    Div(
                        H3(chat['user'], cls="text-base text-black"),
                        P(chat['last_timestamp'], cls="text-xs text-gray-500"),
                        cls="flex justify-between items-center w-full"
                    ),
                    P(f"{chat['last_message']:.35}{'...' if len(chat['last_message']) > 35 else ''}", cls="text-xs text-gray-600"),
                    cls="ml-4 flex-grow border-b border-base-200 pb-3",
                ),
                cls="flex items-center p-2 hover:bg-base-200 rounded-lg transition-colors w-full",
            ),
            href=f"/messages/{chat['user']}",
            cls="no-underline text-current",
        )
        for chat in chats
    ]

    # Replace Container with Main for better structure
    page = Main(
        Div(
            Div(
                *userlist,
                cls="flex flex-col divide-y divide-base-200 bg-base-100 rounded-box shadow",
            ),
            A("Return to List of Apps", href="/", role="button", cls="btn btn-outline mt-6 w-full text-lg"),
            cls="max-w-md mx-auto p-4",
        )
    )

    return Div(
        logo_title_container,
        page
    )

@app.get("/messages/{user_id}/")
def index(user_id: str):
    chat_history: Messages = message_history_db[user_id]
    messages = ast.literal_eval(chat_history.messages)
    senders = ast.literal_eval(chat_history.senders)
    timestamps = ast.literal_eval(chat_history.timestamps)

    page = Main(cls="h-[80vh] flex flex-col bg-base-200")(
        # Header with return button and search icon
        Div(cls="flex justify-between items-center p-2.5 border-b shadow-sm")(
            Div(cls="flex items-center gap-3")(
                A(I(cls="fas fa-arrow-left text-xl"), href="/messages", cls="btn btn-ghost btn-circle"),
                # Placeholder for Avatar
                Div(user_logo if 'group' not in user_id.lower() else group_logo, cls="h-10 mr-3"),
                H1(user_id, cls="text-lg font-bold")
            ),
            Button(
                I(cls="fas fa-search"),
                id="search-toggle",
                cls="btn btn-ghost btn-circle",
                type="button"
            )
        ),
        # Search Bar
        SearchBar(),
        # Chat container with background
        Div(id="chat-container", cls="flex-1 overflow-y-auto scroll-smooth chat-bg")(
            # Messages container
            Div(
                id="chatlist",
                cls="p-4 flex flex-col gap-2",
            )(
                *[
                    ChatMessage(message, sender, timestamp)
                    for message, sender, timestamp in zip(messages, senders, timestamps)
                ]
            ),
        ),
        # Input form
        Form(
            cls="border-t p-2 bg-base-200 flex items-center gap-2",
            hx_post="/messages/send",
            hx_target="#chatlist",
            hx_swap="beforeend",
            hx_trigger="submit",
            _="on submit halt",
        )(
            Group(
                ChatInput(),
                Button(I(cls="fas fa-paper-plane"), cls="btn btn-primary", type="submit"),
                Hidden(user_id, name="interlocutor"),
                cls="flex gap-2",
            )
        ),
        Style("""
            .chat-bg {
                background-color: var(--chat-display-background-color);
            }
            .chat-bubble {
                border-radius: 12px;
                max-width: 75%;
            }
            .chat-end .chat-bubble {
                border-bottom-right-radius: 2px;
            }
            .chat-start .chat-bubble {
                border-bottom-left-radius: 2px;
            }
            .search-highlight {
                transition: background-color 0.3s ease;
            }
            .search-highlight .chat-bubble {
                border: 2px solid #570df8;
            }
            @keyframes flash {
                0%, 100% { background-color: transparent; }
                50% { background-color: rgba(87, 13, 248, 0.1); }
            }
            .flash-highlight {
                animation: flash 1s ease;
            }
            .search-result-item {
                transition: background-color 0.2s ease;
            }
            .search-result-item:hover {
                background-color: #e5e7eb;
            }
        """),
        # Add debugging script
        Script("""
            console.log("Page loaded");
            
            // Test click handler
            document.addEventListener('click', function(e) {
                if (e.target.closest('.search-result-item')) {
                    console.log("Search result clicked via event delegation");
                }
            });
        """)
    )

    return Div(
        logo_title_container,
        page
    )


# Handle the form submission
@app.post("/messages/send")
def send(msg: str, interlocutor: str, messages: list[str] = None):
    if not messages:
        messages = []
    messages.append(msg.rstrip())
    current_time = datetime.now().strftime("%b %d, %I:%M %p")
    add_new_message_to_history(interlocutor, msg.rstrip(), "you", current_time)

    if interlocutor == 'Bob':
        r = "Yes, let's play on Saturday!"
    elif interlocutor == 'Alice':
        r = "Yes, I want to play badminton!"
    else:
        r = random.choice(["I'm a bot!", "I'm a human!", "What's up?", "The stock market is crazy today!"])
    add_new_message_to_history(interlocutor, r, interlocutor, current_time)
    return (
        Div(
            ChatMessage(msg, "you", current_time),
            ChatMessage(r.rstrip(), interlocutor, current_time),
            _="on load call scrollToBottom()",
        ),
        ChatInput(),
    )


def get_message_routes():
    return app.routes


@app.get("/messages_all")
def get_all():
    """Used for rewards"""
    entries = []

    for history in message_history_db():
        history_dict = history.__dict__
        new_dict = {"user": history_dict["user"]}
        
        history_dict["messages"] = ast.literal_eval(history_dict["messages"])
        history_dict["senders"] = ast.literal_eval(history_dict["senders"])
        history_dict["timestamps"] = ast.literal_eval(history_dict["timestamps"])

        new_dict["messages"] = list(zip(history_dict["messages"], history_dict["senders"], history_dict["timestamps"]))
        entries.append(new_dict)
    try:
        json_entries = json.dumps(entries)
    except Exception as e:
        print(f"Error creating JSON response for {entries=}")
        raise e

    return Response(json_entries, headers={"Content-Type": "application/json"})


if __name__ == "__main__":
    print("Warning: Running message app in standalone mode")
    app.routes = get_message_routes()
    serve()
