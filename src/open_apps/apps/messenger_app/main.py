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
import re
import zlib
from src.open_apps.apps.start_page.helper import create_logo_header
from open_apps.frontend import local_hdrs
from open_apps.theme import theme_style


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
            message.classList.add('flash-highlight');
            setTimeout(() => {
                message.classList.remove('flash-highlight');
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
    # Pico and htmx served from apps/assets/vendor rather than a CDN. This app
    # previously loaded picolink (Pico from jsdelivr) plus htmx 1.9.10 from
    # unpkg, on top of the htmx 2.0.4 FastHTML injects by default -- two htmx
    # versions racing on a page, both of which vanish on an offline host.
    *local_hdrs(),
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
# The app renders as a narrow, phone-width column. `set_environment` appends
# the active `layout-<name>` to this so layout CSS can reach <body> itself --
# `split_inbox` needs two panes side by side, which `max-w-lg` cannot fit.
_DEFAULT_BODY_CLS = "p-4 max-w-lg mx-auto"
app = FastHTML(hdrs=_base_hdrs, cls=_DEFAULT_BODY_CLS, default_hdrs=False)

# Static, theme-agnostic component styles. Colors and fonts are design tokens
# from the shared theme (`config/apps/theme/`), emitted per-request by
# `messenger_theme()`.
#
# The chat surface maps onto generic tokens rather than bubble-specific ones:
# the outgoing bubble is the primary action color, the incoming bubble is the
# surface color, and the transcript sits on the page background. Keeping the
# vocabulary generic is what lets a theme file stay app-agnostic -- no theme
# has to know this app has bubbles.
#
# `!important` throughout: daisyUI ships utility classes on these same
# elements and would otherwise win.
_COMPONENT_STYLES = Style(
    """
    body {
        background-color: var(--color-bg);
    }
    /* Global text styling */
    h1, h2, h3, p, div {
        font-family: var(--font-family) !important;
        color: var(--color-fg) !important;
    }

    /* Message list specific styling */
    .text-2xl {
        font-size: calc(var(--font-size-base) * 1.5) !important;
    }

    .text-sm {
        font-size: var(--font-size-sm) !important;
    }

    /* Chat specific styling */
    .chat .chat-header {
        font-family: var(--font-family) !important;
        color: var(--color-muted) !important;
        margin-bottom: calc(var(--font-size-sm) * 0.5);
    }

    .chat .chat-bubble {
        font-family: var(--font-family) !important;
        font-size: var(--font-size-sm) !important;
        color: var(--color-fg) !important;
        padding: calc(var(--font-size-sm) * 0.75) var(--font-size-sm);
        min-height: calc(var(--font-size-sm) * 2);
        display: flex;
        align-items: center;
    }
    .input {
        font-family: var(--font-family) !important;
        font-size: var(--font-size-sm) !important;
        color: var(--color-fg) !important;
        background-color: var(--color-bg) !important;
    }

    /* Chat bubbles. `.chat` in the selector is load-bearing: `.chat
       .chat-bubble` above also sets `color` with !important, and between two
       !important rules the more specific one wins -- a bare
       `.chat-bubble-primary` lost, leaving `--color-fg` (near-black) text on
       the `--color-primary` fill of every sent message. */
    .chat .chat-bubble-primary {
        background-color: var(--color-primary) !important;
        color: var(--color-on-primary) !important;
    }

    .chat .chat-bubble-secondary {
        background-color: var(--color-surface) !important;
    }

    /* daisyUI paints these from its own palette, which no token can reach. */
    .bg-base-100, .bg-base-200 {
        background-color: var(--color-surface) !important;
    }
"""
)


def set_environment(config):
    """Set environment variables for the messenger app"""
    global app, logo_title_container, message_history_db
    # Put the layout on <body> rather than on an inner div: the width cap that
    # makes this app phone-shaped lives on <body>, and a descendant cannot
    # widen its own ancestor. Re-applied on every call, so an MCP
    # `reconfigure` layout swap lands too.
    layout = getattr(config.messenger, "layout", "default")
    app.bodykw["cls"] = f"{_DEFAULT_BODY_CLS} layout-{layout}"
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
    # Note: in future implementations, we might need to preserve
    # the order of the style files, scripts and links to prevent conflicts
    app.hdrs = (*_base_hdrs, _COMPONENT_STYLES)
    app.config = config
    # create database
    db = database(app.config.messenger.database_path)
    message_history_db = db.create(Messages, pk="user")
    populate_database(config, message_history_db)
    logo_title_container = create_logo_header(
        app_config=config.start_page.apps.messages,
        base_url="/messages",
        current_file_path=__file__
    )

def messenger_theme():
    """The active theme's `:root` token block.

    Rendered into the page body (not `app.hdrs`) so live `reconfigure` theme
    swaps take effect without rebuilding the headers.
    """
    return theme_style(app.config, "messenger")


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

# --- Presentation helpers ---------------------------------------------------
# Everything below only changes how stored values are *displayed*. The
# database and `/messages_all` keep the raw strings, and task checks strip
# timestamps before comparing (`_remove_timestamp_from_messenger`), so none of
# this reaches a reward.

# Content files mix "Sep 17, 09:32AM", "Apr 16, 9:00 AM" and the
# `strftime("%b %d, %I:%M %p")` of freshly sent messages. One pattern covers
# all three; anything else is shown verbatim rather than guessed at.
_TIMESTAMP_RE = re.compile(
    r"^\s*([A-Za-z]{3})\s+(\d{1,2}),\s*(\d{1,2}):(\d{2})\s*([AaPp][Mm])\s*$"
)


def _parse_timestamp(timestamp):
    match = _TIMESTAMP_RE.match(timestamp or "")
    if not match:
        return None
    month, day, hour, minute, meridiem = match.groups()
    return month.title(), int(day), int(hour), minute, meridiem.upper()


def display_time(timestamp):
    """One consistent in-thread format: "Sep 17, 9:32 AM"."""
    parsed = _parse_timestamp(timestamp)
    if parsed is None:
        return timestamp or ""
    month, day, hour, minute, meridiem = parsed
    return f"{month} {day}, {hour}:{minute} {meridiem}"


def short_date(timestamp):
    """Chat-list format: just the day, as clients show for older chats.

    Deliberately never "today -> time only": that would make the list depend
    on the date an eval runs, and no seeded conversation is from today.
    """
    parsed = _parse_timestamp(timestamp)
    if parsed is None:
        return timestamp or ""
    month, day, *_ = parsed
    return f"{month} {day}"


def is_group_chat(name):
    # The same heuristic the list preview and the UI-question generator use.
    return "group" in name.lower()


def group_members(senders):
    """Distinct other participants of a conversation, in order of first message."""
    members = []
    for sender in senders:
        if sender != "you" and sender not in members:
            members.append(sender)
    return members


def initials(name):
    """Up to two letters: first letters of the first two words, else the first letter."""
    words = [w for w in re.split(r"[\s_\-]+", name.strip()) if w]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][0].upper()
    return (words[0][0] + words[1][0]).upper()


# How many `.avatar-tone-N` classes `_LAYOUT_STYLES` defines.
AVATAR_TONES = 6


def avatar_tone(name):
    """Stable per name across processes -- `hash()` is salted per run, and a
    contact changing colour between episodes would be a spurious cue."""
    return zlib.crc32(name.encode("utf-8")) % AVATAR_TONES


def _avatar_disc(name, extra_cls=""):
    return Span(
        initials(name),
        cls=f"msg-avatar-disc avatar-tone-{avatar_tone(name)} {extra_cls}".strip(),
    )


def Avatar(name, members=None, size="list"):
    """Initials in a theme-tinted disc; group chats get two overlapping discs.

    `aria-hidden`: the name sits right next to it in every placement, so the
    letters would only prefix each accessible name with noise ("A Alice").
    `.msg-avatar` is kept so compact_list can hide avatars wholesale.
    """
    if members is not None and len(members) >= 2:
        content = (
            _avatar_disc(members[0], "msg-avatar-back"),
            _avatar_disc(members[1], "msg-avatar-front"),
        )
        shape = "is-group"
    else:
        content = (_avatar_disc(name),)
        shape = "is-single"
    return Div(*content, cls=f"msg-avatar msg-avatar-{size} {shape}", aria_hidden="true")


def message_runs(senders):
    """(first_in_run, last_in_run) for each message.

    A run is consecutive messages from one sender; clients show the sender
    once per run and tuck the bubbles together.
    """
    flags = []
    for i, sender in enumerate(senders):
        first = i == 0 or senders[i - 1] != sender
        last = i == len(senders) - 1 or senders[i + 1] != sender
        flags.append((first, last))
    return flags


# Chat message component (renders a chat bubble)
def ChatMessage(message, sender, timestamp=None, first_in_run=True, last_in_run=True):
    """One message. Every message keeps its sender label and time in the DOM
    -- the search script reads both, and an agent reading the accessibility
    tree should not lose who said what -- while `_LAYOUT_STYLES` decides which
    of them a sighted user sees (`msg-first` / `msg-last`)."""
    outgoing = sender == "you"
    if outgoing:
        bubble_class = "chat-bubble-primary"
        chat_class = "chat-end"
    else:
        bubble_class = "chat-bubble-secondary"
        chat_class = "chat-start"
    if timestamp is None:
        timestamp = datetime.now().strftime("%b %d, %I:%M %p")  # Format: Apr 16, 10:30 AM
    run_cls = (" msg-first" if first_in_run else "") + (" msg-last" if last_in_run else "")
    header = Div(sender, cls="chat-header")
    bubble = Div(message, cls=f"chat-bubble {bubble_class}")
    footer = Div(display_time(timestamp), cls="chat-footer")
    # compact_list sets the time on the sender's line, above the text, so
    # the DOM follows suit there; the bubble layouts put it under the bubble.
    if current_layout() == "compact_list":
        parts = (header, footer, bubble)
    else:
        parts = (header, bubble, footer)
    return Div(cls=f"chat {chat_class}{run_cls}")(
        # Incoming messages carry the sender's avatar beside the run's last
        # bubble, the way Messenger and iMessage do.
        None if outgoing else Div(Avatar(sender, size="inline"), cls="chat-image"),
        *parts,
        Hidden(message, name="messages"),
    )

# The input field for the user message. Also used to clear the
# input field after sending a message via an OOB swap
def ChatInput():
    return Input(
        name="msg",
        id="msg-input",
        placeholder="Type a message",
        cls="input msg-compose-input",
        hx_swap_oob="true",
    )

# Search bar component with results area
def SearchBar():
    return Div(
        id="search-bar",
        cls="hidden msg-search"
    )(
        Div(cls="msg-search-row")(
            Input(
                id="search-input",
                placeholder="Search messages...",
                cls="input msg-search-input",
                onkeyup="searchMessages()"
            ),
            Button(
                I(cls="fas fa-times", aria_hidden="true"),
                cls="msg-icon-btn",
                aria_label="Clear search",
                onclick="clearSearch()",
                type="button"
            )
        ),
        Div(id="search-results", cls="msg-search-count"),
        Div(
            id="search-results-container", 
            cls="msg-search-results"
        )
    )

def current_layout():
    """The active structure variant from `config/apps/messenger/layout/`.

    Both variants keep `/messages` and `/messages/{user_id}/` resolving and
    leave `#chatlist` in place, so tasks and rewards (which read
    `/messages_all`) are unaffected.
    """
    config = getattr(app, "config", None)
    if config is None:
        return "default"
    return getattr(config.messenger, "layout", "default")


# Messenger's own look, layered over daisyUI and Pico. Every colour, font,
# radius and spacing is a theme token (`config/apps/theme/`), so the same
# rules hold in dark, solarized, mono and challenging_font.
#
# Selectors are anchored on ids and app classes rather than daisyUI's: the
# daisyUI/Tailwind stylesheets come from a CDN an offline eval node never
# reaches, so these rules have to lay the page out on their own, and when
# the CDN *does* load they have to outrank it (`#chatlist ...`, or
# `!important` where the rule being replaced carries it).
#
# Radii are multiples of `--radius`, including the "pill" shapes
# (`--radius * 999`): a theme that squares everything off (mono, radius 0)
# squares these off too instead of being overridden by a hard-coded circle.
_VISUALLY_HIDDEN = """
        position: absolute !important;
        width: 1px !important;
        height: 1px !important;
        margin: -1px !important;
        padding: 0 !important;
        overflow: hidden !important;
        clip: rect(0 0 0 0) !important;
        white-space: nowrap !important;
        border: 0 !important;
"""

_LAYOUT_STYLES = Style("""
    /* ---------- Chat list (all layouts) ---------- */
    .msg-list { display: flex; flex-direction: column; gap: 2px; }
    .msg-list .msg-link {
        display: block;
        color: inherit;
        text-decoration: none;
        border-radius: calc(var(--radius) * 1.25);
    }
    .msg-list .msg-link:focus-visible { outline: 2px solid var(--color-primary); outline-offset: 2px; }
    .msg-row {
        display: flex;
        align-items: center;
        gap: calc(var(--space) * 1.5);
        padding: var(--space) calc(var(--space) * 1.25);
        border-radius: calc(var(--radius) * 1.25);
        transition: background-color 0.12s ease;
    }
    .msg-link:hover .msg-row { background-color: color-mix(in srgb, var(--color-fg) 6%, var(--color-bg)); }
    /* The open conversation, in whichever pane shows the list. A tint of the
       primary colour survives every theme, unlike daisyUI's base-200. */
    .msg-row.is-selected {
        background-color: color-mix(in srgb, var(--color-primary) 16%, var(--color-surface)) !important;
    }
    .msg-row-body { flex: 1 1 auto; min-width: 0; }
    .msg-row-top { display: flex; align-items: baseline; gap: var(--space); }
    /* Name truncates before it can run into the time; the time never wraps. */
    .msg-list .msg-row-name {
        flex: 1 1 auto;
        min-width: 0;
        margin: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        font-size: var(--font-size-base);
        font-weight: 600;
        line-height: 1.3;
    }
    .msg-row-time {
        flex: none;
        white-space: nowrap;
        font-family: var(--font-family);
        font-size: calc(var(--font-size-sm) * 0.86);
        color: var(--color-muted);
    }
    .msg-list .msg-row-preview {
        margin: 2px 0 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        font-size: var(--font-size-sm);
        line-height: 1.35;
        color: var(--color-muted) !important;
    }

    /* ---------- Avatars ---------- */
    .msg-avatar { position: relative; flex: none; display: inline-flex; }
    .msg-avatar-list { width: 3rem; height: 3rem; }
    .msg-avatar-header { width: 2.5rem; height: 2.5rem; }
    .msg-avatar-inline { width: 1.75rem; height: 1.75rem; }
    .msg-avatar-disc {
        --tone: var(--color-primary);
        display: flex;
        align-items: center;
        justify-content: center;
        width: 100%;
        height: 100%;
        border-radius: calc(var(--radius) * 999);
        background-color: color-mix(in srgb, var(--tone) 22%, var(--color-bg));
        color: color-mix(in srgb, var(--tone) 70%, var(--color-fg));
        font-family: var(--font-family);
        font-size: calc(var(--font-size-base) * 1.05);
        font-weight: 600;
        line-height: 1;
        user-select: none;
    }
    .msg-avatar-header .msg-avatar-disc { font-size: var(--font-size-sm); }
    .msg-avatar-inline .msg-avatar-disc { font-size: calc(var(--font-size-sm) * 0.8); }
    /* Group chats: two members' discs, overlapped. */
    .msg-avatar.is-group .msg-avatar-disc {
        position: absolute;
        width: 68%;
        height: 68%;
        font-size: calc(var(--font-size-sm) * 0.8);
        box-shadow: 0 0 0 2px var(--color-bg);
    }
    .msg-avatar.is-group .msg-avatar-back { top: 0; left: 0; }
    .msg-avatar.is-group .msg-avatar-front { right: 0; bottom: 0; }
    .avatar-tone-0 { --tone: var(--color-primary); }
    .avatar-tone-1 { --tone: var(--color-accent); }
    .avatar-tone-2 { --tone: var(--color-danger); }
    .avatar-tone-3 { --tone: var(--color-neutral); }
    .avatar-tone-4 { --tone: color-mix(in srgb, var(--color-primary) 50%, var(--color-danger)); }
    .avatar-tone-5 { --tone: color-mix(in srgb, var(--color-accent) 50%, var(--color-neutral)); }

    /* ---------- List page + "Return to List of Apps" ---------- */
    .msg-list-page { padding: var(--space) 0; }
    /* Base: a small tonal button, which split_inbox and compact_list quiet
       further below. `default` renders `.is-prominent` instead (see
       `return_to_apps_link`). Pico styles [role=button] as a primary
       button, hence the !important resets. */
    a.msg-apps-link[role="button"] {
        display: inline-flex;
        align-items: center;
        gap: calc(var(--space) * 0.75);
        width: auto;
        margin: calc(var(--space) * 2) 0 0 calc(var(--space) * 1.25);
        padding: calc(var(--space) * 0.625) calc(var(--space) * 1.5);
        border: 0 !important;
        border-radius: calc(var(--radius) * 999);
        box-shadow: none !important;
        background: color-mix(in srgb, var(--color-fg) 6%, var(--color-bg)) !important;
        color: var(--color-fg) !important;
        font-family: var(--font-family);
        font-size: var(--font-size-sm) !important;
        font-weight: 500;
        line-height: 1.3;
        text-decoration: none;
    }
    a.msg-apps-link[role="button"]:hover {
        background: color-mix(in srgb, var(--color-fg) 11%, var(--color-bg)) !important;
    }
    a.msg-apps-link[role="button"]:focus-visible { outline: 2px solid var(--color-primary); outline-offset: 2px; }
    .msg-apps-link .fa-chevron-left { font-size: 0.75em; color: var(--color-muted); }
    /* default: full-width outlined button directly under the list, at body
       size, so a screenshot agent finds the way home at a glance. */
    a.msg-apps-link.is-prominent[role="button"] {
        display: flex;
        justify-content: center;
        width: 100%;
        margin: calc(var(--space) * 2) 0 0;
        padding: calc(var(--space) * 1.25) calc(var(--space) * 2);
        border: 1px solid var(--color-fg) !important;
        border-radius: var(--radius);
        background: transparent !important;
        color: var(--color-fg) !important;
        font-size: var(--font-size-base) !important;
        font-weight: 600;
    }
    a.msg-apps-link.is-prominent[role="button"]:hover {
        background: color-mix(in srgb, var(--color-fg) 6%, var(--color-bg)) !important;
    }

    /* ---------- Thread frame ---------- */
    main.msg-thread {
        display: flex;
        flex-direction: column;
        height: 80vh;
        margin: 0;
        padding: 0;
        overflow: hidden;
        background-color: var(--color-bg);
        border: 1px solid var(--color-border);
        border-radius: calc(var(--radius) * 1.5);
    }
    .msg-thread-header {
        flex: none;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: var(--space);
        padding: var(--space) calc(var(--space) * 1.5);
        border-bottom: 1px solid var(--color-border);
        background-color: var(--color-bg);
    }
    .msg-thread-title { display: flex; align-items: center; gap: calc(var(--space) * 1.25); min-width: 0; }
    .msg-thread-name { min-width: 0; }
    .msg-thread-name h1 {
        margin: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        font-size: var(--font-size-base);
        font-weight: 600;
        line-height: 1.25;
    }
    .msg-thread-name .msg-thread-sub {
        margin: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        font-size: calc(var(--font-size-sm) * 0.9);
        line-height: 1.3;
        color: var(--color-muted) !important;
    }
    .msg-icon-btn {
        flex: none;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 2.25rem;
        height: 2.25rem;
        margin: 0;
        padding: 0;
        border: 0 !important;
        border-radius: calc(var(--radius) * 999);
        box-shadow: none !important;
        background: transparent !important;
        color: var(--color-primary) !important;
        font-size: var(--font-size-base);
        cursor: pointer;
    }
    .msg-icon-btn:hover { background: color-mix(in srgb, var(--color-fg) 8%, transparent) !important; }
    .msg-icon-btn:focus-visible { outline: 2px solid var(--color-primary); outline-offset: 2px; }

    /* Search bar (toggled by the header's search button). */
    .msg-search {
        flex: none;
        display: flex;
        flex-direction: column;
        gap: var(--space);
        padding: var(--space) calc(var(--space) * 1.5);
        border-bottom: 1px solid var(--color-border);
        background-color: var(--color-surface);
    }
    .msg-search.hidden { display: none; }
    .msg-search-row { display: flex; align-items: center; gap: var(--space); }
    .msg-search .msg-search-input {
        flex: 1 1 auto;
        height: 2.25rem;
        margin: 0 !important;
        padding: 0 calc(var(--space) * 1.5) !important;
        border: 1px solid var(--color-border) !important;
        border-radius: calc(var(--radius) * 999) !important;
    }
    .msg-search .msg-search-count { font-size: var(--font-size-sm); font-weight: 600; color: var(--color-muted) !important; }
    .msg-search-count:empty, .msg-search-results:empty { display: none; }
    .msg-search-results { display: flex; flex-direction: column; gap: 2px; max-height: 10rem; overflow-y: auto; }
    .search-result-item { transition: background-color 0.2s ease; }
    .search-result-item:hover { background-color: color-mix(in srgb, var(--color-fg) 6%, var(--color-surface)); }
    .search-highlight .chat-bubble { box-shadow: 0 0 0 2px var(--color-accent) !important; }
    @keyframes msg-flash {
        0%, 100% { background-color: transparent; }
        50% { background-color: color-mix(in srgb, var(--color-accent) 14%, transparent); }
    }
    .flash-highlight { animation: msg-flash 1s ease; }

    .msg-transcript {
        flex: 1 1 auto;
        min-height: 0;
        overflow-y: auto;
        scroll-behavior: smooth;
        background-color: var(--color-bg);
    }
    #chatlist {
        display: flex;
        flex-direction: column;
        padding: calc(var(--space) * 1.5) calc(var(--space) * 1.5) var(--space);
    }

    /* ---------- Messages: bubble layouts (default, split_inbox) ----------
       Outgoing on the right in the primary colour, incoming on the left on
       the surface colour with the sender's avatar beside the last bubble of
       each run. Runs sit tight; a new run gets air above it. `grid-column:
       -2 / -1` is "the last column": the bubble column for incoming (avatar
       gutter + bubble) and the only column for outgoing. */
    #chatlist .chat {
        position: relative;
        display: grid;
        grid-template-columns: minmax(0, 1fr);
        column-gap: var(--space);
        row-gap: 0;
        place-items: start;
        margin: 0;
        padding: 1px 0;
    }
    #chatlist .chat.msg-first { margin-top: calc(var(--space) * 1.25); }
    #chatlist > .chat:first-child { margin-top: 0; }
    #chatlist .chat.chat-start { grid-template-columns: 1.75rem minmax(0, 1fr); }
    #chatlist .chat.chat-end { justify-items: end; }
    #chatlist .chat > .chat-header,
    #chatlist .chat > .chat-bubble,
    #chatlist .chat > .chat-footer { grid-column: -2 / -1; }
    #chatlist .chat > .chat-header { grid-row: 1; }
    #chatlist .chat > .chat-bubble { grid-row: 2; }
    #chatlist .chat > .chat-footer { grid-row: 3; }
    #chatlist .chat > .chat-image { grid-column: 1; grid-row: 2; align-self: end; }
    #chatlist .chat:not(.msg-last) > .chat-image { visibility: hidden; }
    #chatlist .chat .chat-header {
        margin: 0 0 2px;
        padding: 0 calc(var(--font-size-sm) * 0.9);
        font-size: calc(var(--font-size-sm) * 0.86);
        line-height: 1.3;
        color: var(--color-muted) !important;
    }
    #chatlist .chat .chat-bubble {
        display: block;
        width: fit-content;
        max-width: min(75%, 36rem);
        min-width: 0;
        min-height: 0;
        padding: calc(var(--font-size-sm) * 0.55) calc(var(--font-size-sm) * 0.9);
        line-height: 1.4;
        overflow-wrap: anywhere;
        border-radius: calc(var(--radius) * 2.25);
        /* Keeps incoming bubbles visible where surface == background (mono). */
        box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--color-border) 45%, transparent);
    }
    #chatlist .chat.chat-end .chat-bubble { box-shadow: none; }
    /* daisyUI draws a bubble tail as a pseudo-element; Messenger-style runs
       use tucked corners instead. */
    #chatlist .chat .chat-bubble::before { display: none; }
    #chatlist .chat-start:not(.msg-last) .chat-bubble { border-end-start-radius: calc(var(--radius) * 0.5); }
    #chatlist .chat-start:not(.msg-first) .chat-bubble { border-start-start-radius: calc(var(--radius) * 0.5); }
    #chatlist .chat-end:not(.msg-last) .chat-bubble { border-end-end-radius: calc(var(--radius) * 0.5); }
    #chatlist .chat-end:not(.msg-first) .chat-bubble { border-start-end-radius: calc(var(--radius) * 0.5); }
    #chatlist .chat .chat-footer {
        margin-top: 3px;
        padding: 0 calc(var(--font-size-sm) * 0.9);
        font-size: calc(var(--font-size-sm) * 0.8);
        line-height: 1.3;
        color: var(--color-muted) !important;
    }
    /* What a sighted user sees of the per-message metadata. It stays in the
       DOM (visually hidden, not display:none) so the accessibility tree
       still says who sent every message and when:
       - the time only under the last bubble of a run;
       - no label on your own messages -- side and colour already say so;
       - in 1:1 chats no incoming label either, as in every phone client;
         in groups, the sender's name once, above the run. */
    body:not(.layout-compact_list) #chatlist .chat:not(.msg-last) > .chat-footer,
    body:not(.layout-compact_list) #chatlist .chat:not(.msg-first) > .chat-header,
    body:not(.layout-compact_list) #chatlist .chat.chat-end > .chat-header,
    body:not(.layout-compact_list) .msg-thread.is-direct #chatlist .chat > .chat-header {""" + _VISUALLY_HIDDEN + """    }

    /* ---------- Composer ---------- */
    .msg-composer {
        flex: none;
        margin: 0;
        padding: var(--space) calc(var(--space) * 1.25);
        border-top: 1px solid var(--color-border);
        background-color: var(--color-bg);
    }
    /* Pico joins a [role=group]'s children into one segmented control,
       squaring the inner corners -- which is what made the send button a
       hard square. Unjoin them. */
    .msg-composer fieldset.msg-composer-row {
        display: flex;
        align-items: center;
        gap: var(--space);
        width: 100%;
        margin: 0;
        padding: 0;
        border: 0;
        box-shadow: none !important;
    }
    .msg-composer .msg-compose-input {
        flex: 1 1 auto;
        min-width: 0;
        height: 2.5rem;
        margin: 0 !important;
        padding: 0 calc(var(--space) * 2) !important;
        border: 1px solid color-mix(in srgb, var(--color-border) 70%, transparent) !important;
        border-radius: calc(var(--radius) * 999) !important;
        background-color: var(--color-surface) !important;
        box-shadow: none !important;
    }
    .msg-composer .msg-compose-input::placeholder { color: var(--color-muted); opacity: 1; }
    .msg-composer .msg-compose-input:focus {
        outline: none;
        border-color: var(--color-primary) !important;
        box-shadow: 0 0 0 2px color-mix(in srgb, var(--color-primary) 30%, transparent) !important;
    }
    .msg-composer .msg-send {
        flex: none;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 2.5rem;
        height: 2.5rem;
        margin: 0 !important;
        padding: 0 !important;
        border: 0 !important;
        border-radius: calc(var(--radius) * 999) !important;
        box-shadow: none !important;
        background-color: var(--color-primary) !important;
        color: var(--color-on-primary) !important;
        font-size: var(--font-size-sm);
        cursor: pointer;
    }
    /* Mixed toward the text colour rather than `--color-primary-hover`,
       which in the dark theme is too dark for its own on-primary text. */
    .msg-composer .msg-send:hover { background-color: color-mix(in srgb, var(--color-primary) 85%, var(--color-fg)) !important; }
    .msg-composer .msg-send:focus-visible { outline: 2px solid var(--color-primary); outline-offset: 2px; }

    @media (prefers-reduced-motion: reduce) {
        .msg-transcript { scroll-behavior: auto; }
        .flash-highlight { animation: none; }
        .msg-row { transition: none; }
    }

    /* ---------- split_inbox: Messenger / Slack desktop ----------
       One framed window: a fixed chat-list column that scrolls on its own
       and a reading pane at the same height -- an empty state on
       `/messages`, the thread once one is open. The body cap has to lift
       first: `max-w-lg` is a utility class, so element+class outranks it. */
    body.layout-split_inbox { max-width: 76rem; }
    main.msg-split-main { max-width: none; margin: 0; padding: 0; }
    .messenger-split {
        display: flex;
        align-items: stretch;
        height: 80vh;
        overflow: hidden;
        background-color: var(--color-bg);
        border: 1px solid var(--color-border);
        border-radius: calc(var(--radius) * 1.5);
    }
    .messenger-split > .msg-list-pane {
        flex: 0 0 22rem;
        max-width: 22rem;
        display: flex;
        flex-direction: column;
        justify-content: flex-start;  /* Pico spreads a <nav>'s children */
        min-height: 0;
        overflow: hidden;
        padding: calc(var(--space) * 1.5) var(--space) var(--space);
        border-right: 1px solid var(--color-border);
    }
    .messenger-split .msg-list-heading {
        margin: 0 0 var(--space);
        padding: 0 calc(var(--space) * 1.25);
        font-size: var(--font-size-heading);
        font-weight: 700;
        line-height: 1.2;
    }
    .messenger-split .msg-list-scroll { flex: 1; min-height: 0; overflow-y: auto; }
    .messenger-split > .msg-thread-pane { flex: 1; min-width: 0; display: flex; }
    .messenger-split > .msg-thread-pane > main.msg-thread {
        width: 100%;
        max-width: none;
        height: 100%;
        margin: 0;
        border: 0;
        border-radius: 0;
    }
    /* Pane chrome, so the way home is a quiet link at the foot of the list. */
    .messenger-split a.msg-apps-link[role="button"] {
        align-self: flex-start;
        margin: var(--space) 0 0;
        padding: calc(var(--space) * 0.5) calc(var(--space) * 1.25);
        background: transparent !important;
        color: var(--color-muted) !important;
    }
    .messenger-split a.msg-apps-link[role="button"]:hover {
        background: color-mix(in srgb, var(--color-fg) 6%, transparent) !important;
        color: var(--color-fg) !important;
    }
    /* The list is already on screen, so the thread's back arrow -- which
       only leads to that same list -- goes, as it does on desktop clients. */
    .messenger-split .thread-back { display: none; }
    .msg-empty-pane { align-items: center; justify-content: center; }
    .msg-empty { display: flex; flex-direction: column; align-items: center; gap: var(--space); }
    .msg-empty-icon { font-size: calc(var(--font-size-base) * 3); color: color-mix(in srgb, var(--color-muted) 55%, transparent); }
    .msg-empty .msg-empty-text { margin: 0; font-size: var(--font-size-sm); color: var(--color-muted) !important; }
    /* Too narrow for two panes: stack them, list first, like a phone. */
    @media (max-width: 48rem) {
        .messenger-split { flex-direction: column; height: auto; }
        .messenger-split > .msg-list-pane {
            flex-basis: auto;
            max-width: none;
            border-right: 0;
            border-bottom: 1px solid var(--color-border);
        }
        .messenger-split > .msg-thread-pane > main.msg-thread { height: 80vh; }
        .messenger-split > .msg-empty-pane { display: none; }
    }

    /* ---------- compact_list: Slack compact mode ----------
       No avatars, one line per chat, and a thread of flat lines -- sender
       and time on the first line of a run, continuation lines as bare
       text. Sender is carried by the label, not by which side it sits. */
    .layout-compact_list .msg-avatar,
    .layout-compact_list #chatlist .chat > .chat-image { display: none; }
    .layout-compact_list .msg-list { gap: 0; border-top: 1px solid var(--color-border); }
    .layout-compact_list .msg-list .msg-link { border-radius: 0; border-bottom: 1px solid var(--color-border); }
    .layout-compact_list .msg-row { gap: 0; padding: calc(var(--space) * 0.625) var(--space); border-radius: 0; }
    .layout-compact_list .msg-row-body { display: flex; align-items: baseline; gap: var(--space); }
    .layout-compact_list .msg-list .msg-row-name { flex: 0 1 auto; max-width: 45%; font-size: var(--font-size-sm); }
    .layout-compact_list .msg-list .msg-row-preview { flex: 1 1 auto; min-width: 0; margin: 0; }
    .layout-compact_list a.msg-apps-link[role="button"] {
        margin-left: 0;
        border-radius: var(--radius);
        background: transparent !important;
        color: var(--color-muted) !important;
        padding-left: var(--space);
    }
    .layout-compact_list main.msg-thread { border-radius: var(--radius); }
    .layout-compact_list .msg-thread-header { padding: calc(var(--space) * 0.75) var(--space); }
    .layout-compact_list #chatlist { padding: var(--space) 0; }
    .layout-compact_list #chatlist .chat {
        grid-template-columns: auto minmax(0, 1fr);
        place-items: baseline start;
        column-gap: var(--space);
        padding: 1px calc(var(--space) * 1.5);
    }
    .layout-compact_list #chatlist .chat.msg-first { margin-top: var(--space); padding-top: 3px; }
    .layout-compact_list #chatlist > .chat:first-child { margin-top: 0; }
    .layout-compact_list #chatlist .chat:hover { background-color: color-mix(in srgb, var(--color-fg) 4%, var(--color-bg)); }
    .layout-compact_list #chatlist .chat > .chat-header {
        grid-row: 1;
        grid-column: 1;
        margin: 0;
        padding: 0;
        font-size: var(--font-size-sm);
        font-weight: 700;
        color: var(--color-fg) !important;
    }
    .layout-compact_list #chatlist .chat.chat-end > .chat-header { color: var(--color-primary) !important; }
    .layout-compact_list #chatlist .chat > .chat-footer { grid-row: 1; grid-column: 2; margin: 0; padding: 0; }
    .layout-compact_list #chatlist .chat > .chat-bubble {
        grid-row: 2;
        grid-column: 1 / -1;
        width: auto;
        max-width: none;
        padding: 0;
        border-radius: 0 !important;
        box-shadow: none;
        background-color: transparent !important;
        color: var(--color-fg) !important;
    }
    .layout-compact_list #chatlist .chat:not(.msg-first) > .chat-header,
    .layout-compact_list #chatlist .chat:not(.msg-first) > .chat-footer {""" + _VISUALLY_HIDDEN + """    }
    /* Slack's composer is a box, not a pill. */
    .layout-compact_list .msg-composer .msg-compose-input,
    .layout-compact_list .msg-composer .msg-send { border-radius: var(--radius) !important; }
""")


def conversation_list(selected: str = None):
    """The list of chats, shared by `/messages` and the split_inbox thread view.

    `selected` marks the open conversation when both panes are on screen; it
    is None on the standalone list page.
    """
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
                "last_timestamp": timestamps[-1] if timestamps else "",
                "members": group_members(senders),
            })
        else:
            chats.append({
                "user": history.user,
                "last_message": "No messages yet",
                "last_timestamp": "",
                "members": [],
            })

    compact = current_layout() == "compact_list"

    def row(chat):
        user = chat["user"]
        # The server-side cut stays at 35 characters so the text an agent
        # reads is as long as it always was -- adversarial content variants
        # plant their payload in the last message, and a full-length preview
        # would put all of it on the list page. CSS ellipsis handles rows too
        # narrow for even that.
        last = chat["last_message"]
        preview = f"{last[:35].rstrip()}…" if len(last) > 35 else last
        name = H3(user, cls="msg-row-name")
        when = Span(short_date(chat["last_timestamp"]), cls="msg-row-time")
        snippet = P(preview, cls="msg-row-preview")
        avatar = Avatar(user, chat["members"] if is_group_chat(user) else None)
        if compact:
            # One line per chat, read left to right: who, what, when.
            body = Div(name, snippet, when, cls="msg-row-body")
        else:
            body = Div(Div(name, when, cls="msg-row-top"), snippet, cls="msg-row-body")
        return A(
            Div(
                avatar,
                body,
                cls="msg-row" + (" is-selected" if user == selected else ""),
            ),
            href=f"/messages/{user}",
            cls="msg-link",
            # Exposes the open chat to the accessibility tree, not just as a
            # background tint an agent reading the axtree would never see.
            aria_current="page" if user == selected else None,
        )

    return Div(*[row(chat) for chat in chats], cls="msg-list")


def list_pane(selected: str = None):
    """split_inbox's left column, identical on `/messages` and on a thread so
    opening a chat does not shift the list."""
    # A landmark, so the conversation list is reachable as a region of its
    # own rather than as anonymous divs beside the thread.
    return Nav(
        H2("Chats", cls="msg-list-heading"),
        Div(conversation_list(selected=selected), cls="msg-list-scroll"),
        return_to_apps_link(),
        cls="msg-list-pane",
        aria_label="Conversations",
    )


def return_to_apps_link():
    """The way home, under the chat list: same text, href and role everywhere.

    In `default` it stays a full-width outlined button: the navigation tasks
    (`config/tasks/original_tasks.yaml`) have screenshot agents find it, and
    the UI question bank asks for this label below the list. The other
    layouts demote it to quiet chrome, as a desktop or compact client would.
    """
    if current_layout() == "default":
        return A(
            "Return to List of Apps",
            href="/",
            role="button",
            cls="msg-apps-link is-prominent",
        )
    return A(
        I(cls="fas fa-chevron-left", aria_hidden="true"),
        "Return to List of Apps",
        href="/",
        role="button",
        cls="msg-apps-link",
    )


# the main screen, create a page that displays a list of users. Each user can be clicked on to display the detailed messages
@app.get("/messages")
def index():
    if current_layout() == "split_inbox":
        # Desktop clients keep the two-pane frame even before a chat is
        # opened: the list on the left, an empty reading pane on the right.
        # Everything stays inside <main> so `main a[href^='/messages/']`
        # still finds the rows.
        page = Main(
            Div(
                list_pane(),
                Div(
                    Div(
                        I(cls="far fa-comments msg-empty-icon", aria_hidden="true"),
                        P("Select a conversation to start messaging", cls="msg-empty-text"),
                        cls="msg-empty",
                    ),
                    cls="msg-thread-pane msg-empty-pane",
                ),
                cls="messenger-split",
            ),
            cls="msg-split-main",
        )
    else:
        page = Main(
            Div(
                conversation_list(),
                return_to_apps_link(),
                cls="msg-list-page",
            )
        )

    # The active layout is a class on <body> (see `set_environment`), so no
    # wrapper class is needed here.
    return Div(
        messenger_theme(),
        _LAYOUT_STYLES,
        logo_title_container,
        page,
    )

@app.get("/messages/{user_id}/")
def index(user_id: str):
    chat_history: Messages = message_history_db[user_id]
    messages = ast.literal_eval(chat_history.messages)
    senders = ast.literal_eval(chat_history.senders)
    timestamps = ast.literal_eval(chat_history.timestamps)

    group = is_group_chat(user_id)
    members = group_members(senders)
    runs = message_runs(senders)

    page = Main(cls="msg-thread " + ("is-group" if group else "is-direct"))(
        # Thread header: back, who you are talking to, search.
        Div(cls="msg-thread-header")(
            Div(cls="msg-thread-title")(
                A(
                    I(cls="fas fa-arrow-left", aria_hidden="true"),
                    href="/messages",
                    cls="thread-back msg-icon-btn",
                    aria_label="Back",
                ),
                Avatar(user_id, members if group else None, size="header"),
                Div(cls="msg-thread-name")(
                    H1(user_id),
                    # Group headers name the members, as every client does.
                    P(", ".join(members), cls="msg-thread-sub") if group and members else None,
                ),
            ),
            Button(
                I(cls="fas fa-search", aria_hidden="true"),
                id="search-toggle",
                cls="msg-icon-btn",
                type="button",
                aria_label="Search",
            ),
        ),
        # Search Bar
        SearchBar(),
        # Chat container with background
        Div(id="chat-container", cls="msg-transcript chat-bg")(
            # Messages container
            Div(id="chatlist")(
                *[
                    ChatMessage(message, sender, timestamp, first, last)
                    for (message, sender, timestamp), (first, last) in zip(
                        zip(messages, senders, timestamps), runs
                    )
                ]
            ),
        ),
        # Input form
        Form(
            cls="msg-composer",
            hx_post="/messages/send",
            hx_target="#chatlist",
            hx_swap="beforeend",
            hx_trigger="submit",
            _="on submit halt",
        )(
            Group(
                ChatInput(),
                Button(
                    I(cls="fas fa-paper-plane", aria_hidden="true"),
                    cls="msg-send",
                    type="submit",
                    aria_label="Send",
                ),
                Hidden(user_id, name="interlocutor"),
                cls="msg-composer-row",
            )
        ),
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

    layout = current_layout()
    if layout == "split_inbox":
        # Desktop-mail arrangement: the chat list stays on screen next to the
        # open thread instead of being a separate page. The thread is still at
        # its own URL, so every existing link and task navigation still works.
        body = Div(
            list_pane(selected=user_id),
            Div(page, cls="msg-thread-pane"),
            cls="messenger-split",
        )
    else:
        body = page

    return Div(
        messenger_theme(),
        _LAYOUT_STYLES,
        logo_title_container,
        body,
    )


# When a sent message continues your own run, the bubble that used to end
# that run is already on the page with `msg-last` (time shown, rounded end
# corner). The run grouping is CSS-only, so demoting it is one class. htmx
# runs inline scripts in swapped content; the script sits inside the
# response wrapper, so the new outgoing message is the wrapper's first
# `.chat` and the bubble to demote is the `.chat` just before it. The
# previous bubble has no stable id to target with an out-of-band swap (the
# search script reassigns `.chat` ids), hence a script rather than hx-swap-oob.
_CLOSE_PREVIOUS_RUN_END = Script("""
    (function () {
        var wrapper = document.currentScript && document.currentScript.parentElement;
        var sent = wrapper && wrapper.querySelector('.chat');
        if (!sent) return;
        var chats = Array.prototype.slice.call(document.querySelectorAll('#chatlist .chat'));
        var previous = chats[chats.indexOf(sent) - 1];
        if (previous && previous.classList.contains('chat-end')) {
            previous.classList.remove('msg-last');
        }
    })();
""", cls="msg-close-run")


# Handle the form submission
@app.post("/messages/send")
def send(msg: str, interlocutor: str, messages: list[str] = None):
    if not messages:
        messages = []
    messages.append(msg.rstrip())
    current_time = datetime.now().strftime("%b %d, %I:%M %p")
    # Whether the new message continues a run of your own messages, read
    # before it is stored. The reply always starts (and ends) its own run.
    previous = ast.literal_eval(message_history_db[interlocutor].senders)
    continues_run = bool(previous) and previous[-1] == "you"
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
            ChatMessage(msg, "you", current_time, first_in_run=not continues_run),
            ChatMessage(r.rstrip(), interlocutor, current_time),
            _CLOSE_PREVIOUS_RUN_END if continues_run else None,
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
