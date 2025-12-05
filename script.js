document.addEventListener("DOMContentLoaded", () => {
  // --- HELPERS ---
  const $ = (id) => document.getElementById(id)
  const h = (tag, props = {}, ...children) => {
    const el = document.createElement(tag)
    Object.entries(props).forEach(([k, v]) => {
      if (k.startsWith("on")) el.addEventListener(k.substring(2).toLowerCase(), v)
      else if (k === "style") Object.assign(el.style, v)
      else if (k === "dataset") Object.assign(el.dataset, v)
      else if (k === "innerHTML") el.innerHTML = v
      else el[k] = v
    })
    children.flat().forEach((child) => child && el.append(child))
    return el
  }

  // --- STATE & CONFIG ---
  const state = {
    isMinimized: false,
    isHidden: false,
    unread: 0,
    isProcessing: false,
    history: [],
    auth: false,
    abortCtrl: null,
    lastUserMsg: "",
  }
  const CFG = {
    BACKEND: "http://127.0.0.1:5000",
    MIN_W: 300,
    MIN_H: 400,
    MAX_W: 800,
    MAX_H: 700,
  }

  // --- UI ELEMENTS ---
  const els = {
    container: $("chatbotContainer"),
    header: $("chatbotHeader"),
    msgs: $("chatbotMessages"),
    input: $("chatbotInput"),
    sendBtn: $("chatbotSend"),
    badge: $("newMessageBadge"),
    notif: $("notification"),
  }

  // --- INTERACTION (Drag & Resize) ---
  const interact = {
    mode: null, // 'drag' | 'resize'
    start: { x: 0, y: 0, w: 0, h: 0, l: 0, t: 0 },
    resizer: null,
    
    init() {
      // Create resizers
      ["top-left", "top-right", "bottom-left", "bottom-right", "left", "right", "top", "bottom"].forEach(pos => {
        els.container.appendChild(h("div", { className: `chatbot-resizer resizer-${pos}`, dataset: { pos } }))
      })
      
      // Listeners
      els.container.addEventListener("mousedown", this.startAction.bind(this))
      document.addEventListener("mousemove", this.move.bind(this))
      document.addEventListener("mouseup", this.end.bind(this))
      // Touch support omitted for brevity/optimization as per request, can add if needed
    },

    startAction(e) {
      const resizer = e.target.closest(".chatbot-resizer")
      const header = e.target.closest(".chatbot-header")
      
      if (resizer) {
        this.mode = "resize"
        this.resizer = resizer.dataset.pos
      } else if (header) {
        this.mode = "drag"
      } else return

      e.preventDefault()
      const rect = els.container.getBoundingClientRect()
      this.start = { x: e.clientX, y: e.clientY, w: rect.width, h: rect.height, l: rect.left, t: rect.top }
      els.container.classList.add("resizing")
      els.container.style.transition = "none"
    },

    move(e) {
      if (!this.mode) return
      e.preventDefault()
      const dx = e.clientX - this.start.x
      const dy = e.clientY - this.start.y

      if (this.mode === "drag") {
        const x = Math.max(0, Math.min(this.start.l + dx, window.innerWidth - this.start.w))
        const y = Math.max(0, Math.min(this.start.t + dy, window.innerHeight - this.start.h))
        Object.assign(els.container.style, { left: `${x}px`, top: `${y}px`, right: "auto", bottom: "auto" })
      } else if (this.mode === "resize") {
        let { w, h, l, t } = this.start
        if (this.resizer.includes("right")) w = Math.min(CFG.MAX_W, Math.max(CFG.MIN_W, w + dx))
        if (this.resizer.includes("left")) {
          const nw = Math.min(CFG.MAX_W, Math.max(CFG.MIN_W, w - dx))
          if (nw !== w) { l += w - nw; w = nw }
        }
        if (this.resizer.includes("bottom")) h = Math.min(CFG.MAX_H, Math.max(CFG.MIN_H, h + dy))
        if (this.resizer.includes("top")) {
          const nh = Math.min(CFG.MAX_H, Math.max(CFG.MIN_H, h - dy))
          if (nh !== h) { t += h - nh; h = nh }
        }
        Object.assign(els.container.style, { width: `${w}px`, height: `${h}px`, left: `${l}px`, top: `${t}px` })
      }
    },

    end() {
      if (this.mode === "drag" && state.isMinimized) restoreChatbot()
      this.mode = null
      els.container.classList.remove("resizing")
      els.container.style.transition = ""
    }
  }

  // --- MESSAGING ---
  const renderMessage = (msg, isUser, id = Date.now().toString(), rowNum = 0) => {
    let msgDiv = els.msgs.querySelector(`.message[data-id="${id}"]`)
    const isUpdate = !!msgDiv
    
    const contentHtml = msg.split("\n").filter(t => t.trim()).map(t => `<div>${t}</div>`).join("")
    
    if (!msgDiv) {
      msgDiv = h("div", { className: `message ${isUser ? "user" : "bot"}`, dataset: { id, rowNumber: rowNum } })
      els.msgs.appendChild(msgDiv)
      if (!isUser) {
          state.history.push({ id, role: "assistant", content: msg })
          if (state.history.length > 10) state.history = state.history.slice(-10)
      } else {
          state.lastUserMsg = msg
          state.history.push({ id, role: "user", content: msg })
      }
    } else {
      // Update existing
      if (rowNum) msgDiv.dataset.rowNumber = rowNum
      const histItem = state.history.find(i => i.id === id)
      if (histItem) histItem.content = msg
    }

    // Content & Actions
    msgDiv.innerHTML = "" // Clear to rebuild
    const bubble = h("div", { className: "message-bubble", innerHTML: contentHtml })
    
    // Copy Btn (Top Right)
    const copyBtn = h("button", {
      className: "copy-message-btn",
      title: "Copiar",
      innerHTML: '<i class="fas fa-copy"></i>',
      onclick: (e) => { e.stopPropagation(); copyToClipboard(msg) }
    })

    msgDiv.append(bubble, copyBtn)

    // Footer Actions
    if (isUser) {
      msgDiv.append(h("div", { className: "message-footer", style: { display: "flex", justifyContent: "flex-end", marginTop: "5px" } },
        h("button", {
          className: "edit-message-btn",
          innerHTML: '<i class="fas fa-pen"></i> Editar',
          style: { background: "none", border: "none", color: "#aaa", cursor: "pointer", fontSize: "0.8rem" },
          onclick: (e) => { e.stopPropagation(); startEdit(id, msg, msgDiv.dataset.rowNumber) }
        })
      ))
    } else {
      // Bot Actions (Regenerate, Like, Dislike)
      const actions = h("div", { className: "message-actions" },
        h("button", { className: "action-btn regenerate-btn", title: "Regenerar", innerHTML: '<i class="fas fa-sync-alt"></i>', onclick: () => processMessage(state.lastUserMsg) }),
        h("button", { className: "action-btn like-btn", title: "Útil", innerHTML: '<i class="far fa-thumbs-up"></i>', onclick: (e) => handleFeedback(id, true, e.currentTarget) }),
        h("button", { className: "action-btn dislike-btn", title: "No útil", innerHTML: '<i class="far fa-thumbs-down"></i>', onclick: (e) => handleFeedback(id, false, e.currentTarget) })
      )
      msgDiv.append(actions)
    }

    // Timestamp
    msgDiv.append(h("div", { className: "timestamp", textContent: new Date().toLocaleTimeString("es-PE", { hour: "2-digit", minute: "2-digit" }) }))
    
    els.msgs.scrollTop = els.msgs.scrollHeight
    
    if (state.isMinimized && !isUser) {
      state.unread++
      els.badge.textContent = state.unread > 9 ? "9+" : state.unread
      els.badge.style.display = "flex"
    }
    
    return id
  }

  const addLinkMessage = (text, linkTxt, url) => {
    const id = renderMessage(text, false)
    const bubble = els.msgs.querySelector(`.message[data-id="${id}"] .message-bubble`)
    bubble.append(h("div", { style: { marginTop: "10px" } },
      h("a", { href: url, target: "_blank", textContent: linkTxt, style: { color: "#1c2682", fontWeight: "bold", textDecoration: "underline" } })
    ))
  }

  const showTyping = () => {
    if (!$("typingIndicator")) els.msgs.append(h("div", { id: "typingIndicator", className: "typing-indicator" }, h("span"), h("span"), h("span")))
    els.msgs.scrollTop = els.msgs.scrollHeight
  }
  const hideTyping = () => $("typingIndicator")?.remove()

  // --- LOGIC ---
  const copyToClipboard = async (text) => {
    try {
      await navigator.clipboard.writeText(text)
      showNotif("Copiado", "Texto copiado", "success")
    } catch {
      const ta = h("textarea", { value: text, style: { position: "fixed", left: "-9999px" } })
      document.body.append(ta); ta.select(); document.execCommand("copy"); ta.remove()
      showNotif("Copiado", "Texto copiado", "success")
    }
  }

  const showNotif = (title, msg, type = "info") => {
    const n = els.notif
    $("notificationTitle").textContent = title
    $("notificationMessage").textContent = msg
    n.querySelector("i").className = `fas fa-${type === "success" ? "check-circle" : type === "error" ? "exclamation-circle" : "info-circle"}`
    n.className = `notification ${type} show`
    setTimeout(() => n.classList.remove("show"), 4000)
  }

  const startEdit = (id, text, rowNum) => {
    els.input.value = text
    els.input.focus()
    Object.assign(els.input.dataset, { editId: id, editRow: rowNum })
    
    // Remove from UI and History to prevent duplication
    const msgDiv = els.msgs.querySelector(`.message[data-id="${id}"]`)
    if (msgDiv) {
        const next = msgDiv.nextElementSibling
        if (next && next.classList.contains("bot")) {
            // Remove bot response from history
            const botId = next.dataset.id
            state.history = state.history.filter(h => h.id !== botId)
            next.remove()
        }
        msgDiv.remove()
        // Remove user message from history
        state.history = state.history.filter(h => h.id !== id)
    }
  }

  const updateSendBtn = (stop) => {
    els.sendBtn.innerHTML = stop ? '<i class="fas fa-stop"></i>' : '<i class="fas fa-paper-plane"></i>'
    els.sendBtn.title = stop ? "Detener" : "Enviar"
    stop ? els.sendBtn.classList.add("stop") : els.sendBtn.classList.remove("stop")
  }

  const processMessage = async (msg) => {
    if (state.isProcessing) return
    state.isProcessing = true
    
    const isEdit = !!els.input.dataset.editId
    const editId = els.input.dataset.editId
    const rowNum = parseInt(els.input.dataset.editRow || 0)
    
    // Clear edit state
    delete els.input.dataset.editId
    delete els.input.dataset.editRow

    // Render user message and capture ID
    const currentMsgId = isEdit ? renderMessage(msg, true, editId, rowNum) : renderMessage(msg, true)

    showTyping()
    updateSendBtn(true)

    try {
      state.abortCtrl = new AbortController()
      const res = await fetch(`${CFG.BACKEND}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: msg, history: state.history.slice(-6), row_number: rowNum }),
        signal: state.abortCtrl.signal
      })
      
      const data = await res.json()
      
      if (res.status === 401 || data.error === "not_authenticated") {
        state.auth = false
        const authRes = await fetch(`${CFG.BACKEND}/api/auth/url`).then(r => r.json())
        if (authRes.auth_url) addLinkMessage("Requiere autorización:", "Autorizar Google", authRes.auth_url)
        else renderMessage("Error de autenticación.", false)
      } else if (data.success) {
        renderMessage(data.response, false, undefined, data.row_number)
        // Update user message with new row number if assigned
        const userMsg = els.msgs.querySelector(`.message[data-id="${currentMsgId}"]`)
        if(userMsg && data.row_number) userMsg.dataset.rowNumber = data.row_number
      } else {
        renderMessage(`Error: ${data.error}`, false)
      }
    } catch (e) {
      if (e.name === "AbortError") {
        // Restore input and remove message if aborted
        els.input.value = msg
        els.input.focus()
        const msgDiv = els.msgs.querySelector(`.message[data-id="${currentMsgId}"]`)
        if (msgDiv) msgDiv.remove()
        state.history = state.history.filter(h => h.id !== currentMsgId)
      } else {
        renderMessage("Error de conexión.", false)
      }
    } finally {
      hideTyping()
      state.isProcessing = false
      state.abortCtrl = null
      updateSendBtn(false)
    }
  }

  const handleFeedback = async (id, isLike, btn) => {
    const type = isLike ? "like" : "dislike"
    const isActive = btn.classList.contains("active")
    
    // Toggle visual state
    if (isActive) {
        btn.classList.remove("active")
        // Send 'none' to remove feedback
        await sendFeedback(id, "none", "", btn)
    } else {
        // Deactivate sibling
        const sibling = isLike ? btn.nextElementSibling : btn.previousElementSibling
        if (sibling) sibling.classList.remove("active")
        
        btn.classList.add("active")
        
        let comment = ""
        if (!isLike) {
            comment = prompt("¿Por qué no fue útil? (Opcional)") || ""
        }
        
        await sendFeedback(id, type, comment, btn)
    }
  }

  const sendFeedback = async (id, type, comment, btn) => {
    try {
        const msgDiv = els.msgs.querySelector(`.message[data-id="${id}"]`)
        const rowNumber = msgDiv ? parseInt(msgDiv.dataset.rowNumber || 0) : 0
        
        await fetch(`${CFG.BACKEND}/api/feedback`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                message_id: id,
                feedback_type: type,
                comment: comment,
                row_number: rowNumber
            })
        })
        console.log(`[Chatbot] Feedback ${type} enviado para ID ${id}`)
    } catch (e) {
        console.error("[Chatbot] Error enviando feedback:", e)
        showNotif("Error", "No se pudo registrar tu opinión", "error")
        // Revert visual state on error
        btn.classList.remove("active")
    }
  }

  const restoreChatbot = () => {
    els.container.classList.remove("minimized", "hidden")
    state.isMinimized = false
    state.unread = 0
    els.badge.style.display = "none"
  }

  // --- INITIALIZATION ---
  interact.init()

  // Event Listeners
  $("minimizeChatbot").onclick = (e) => {
    e.stopPropagation()
    state.isMinimized = !state.isMinimized
    els.container.classList.toggle("minimized", state.isMinimized)
    if (state.isMinimized) els.container.style.height = "" // Reset height for mini
  }
  
  $("closeChatbot").onclick = (e) => {
    e.stopPropagation()
    els.container.classList.add("hidden")
    state.isHidden = true
  }

  els.sendBtn.onclick = (e) => {
    if (state.isProcessing) {
      e.preventDefault()
      state.abortCtrl?.abort()
    } else {
      const msg = els.input.value.trim()
      if (msg) {
        processMessage(msg)
        els.input.value = ""
      }
    }
  }

  els.input.onkeypress = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      els.sendBtn.click()
    }
  }

  // Check Auth
  fetch(`${CFG.BACKEND}/api/auth/status`).then(r => r.json()).then(d => state.auth = d.authenticated)

  // Suggestions
  document.querySelectorAll(".suggestion-chip").forEach(chip => {
    chip.onclick = () => {
      els.input.value = chip.dataset.message
      els.sendBtn.click()
    }
  })
})
