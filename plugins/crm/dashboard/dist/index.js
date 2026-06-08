/**
 * Hermes CRM — Dashboard Plugin
 *
 * Contacts with light status/tags + per-contact conversation history.
 * Calls the plugin backend at /api/plugins/crm/. Conversations are resolved
 * server-side from the gateway sessions + SessionDB (never duplicated).
 *
 * Plain IIFE, no build step. Uses window.__HERMES_PLUGIN_SDK__ for React +
 * shadcn primitives, mirroring the kanban plugin bundle.
 */
(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK) return;

  const { React } = SDK;
  const h = React.createElement;
  const {
    Card, CardContent, Badge, Button, Input, Label, Select, SelectOption,
  } = SDK.components;
  const { useState, useEffect, useCallback } = SDK.hooks;
  const cn = (SDK.utils && SDK.utils.cn) || function () {
    return Array.prototype.filter.call(arguments, Boolean).join(" ");
  };
  const timeAgo = (SDK.utils && SDK.utils.timeAgo) || function (ts) { return ts || ""; };

  const API = "/api/plugins/crm";

  // --- status presentation -------------------------------------------------
  const STATUS_ORDER = ["lead", "active", "customer", "archived"];
  const STATUS_LABEL = {
    lead: "Lead", active: "Active", customer: "Customer", archived: "Archived",
  };
  const STATUS_CLASS = {
    lead: "crm-badge crm-badge--lead",
    active: "crm-badge crm-badge--active",
    customer: "crm-badge crm-badge--customer",
    archived: "crm-badge crm-badge--archived",
  };

  function parseApiError(err) {
    const raw = (err && err.message) ? String(err.message) : String(err || "");
    const m = raw.match(/^(\d{3}):\s*(.*)$/s);
    const body = m ? m[2] : raw;
    try {
      const parsed = JSON.parse(body);
      if (parsed && typeof parsed.detail === "string") return parsed.detail;
    } catch (_e) { /* not JSON */ }
    return body || raw;
  }

  function api(path, opts) {
    return SDK.fetchJSON(`${API}${path}`, opts);
  }

  // -------------------------------------------------------------------------
  // Small presentational helpers
  // -------------------------------------------------------------------------
  function StatusBadge(props) {
    const s = props.status || "lead";
    return h("span", { className: STATUS_CLASS[s] || STATUS_CLASS.lead },
      STATUS_LABEL[s] || s);
  }

  function ChannelChips(props) {
    const handles = props.handles || [];
    if (!handles.length) return null;
    return h("span", { className: "crm-chips" },
      handles.map((hd, i) =>
        h("span", { key: i, className: "crm-chip", title: `${hd.platform}: ${hd.user_id}` },
          hd.platform)));
  }

  // -------------------------------------------------------------------------
  // Contact list (left column)
  // -------------------------------------------------------------------------
  function ContactList(props) {
    const { contacts, total, selectedId, onSelect } = props;
    return h("div", { className: "crm-list" },
      h("div", { className: "crm-list-meta" }, `${contacts.length} of ${total}`),
      contacts.length === 0
        ? h("div", { className: "crm-empty" }, "No contacts match.")
        : contacts.map((c) =>
            h("button", {
              key: c.id,
              className: cn("crm-row", c.id === selectedId && "crm-row--active"),
              onClick: () => onSelect(c.id),
            },
              h("div", { className: "crm-row-top" },
                h("span", { className: "crm-row-name" }, c.display_name),
                h(StatusBadge, { status: c.status })),
              h("div", { className: "crm-row-sub" },
                h(ChannelChips, { handles: c.handles }),
                (c.tags || []).slice(0, 3).map((t, i) =>
                  h("span", { key: i, className: "crm-tag" }, t))))));
  }

  // -------------------------------------------------------------------------
  // Conversation thread (messages for one session)
  // -------------------------------------------------------------------------
  function Thread(props) {
    const { sessionId, onBack } = props;
    const [messages, setMessages] = useState(null);
    const [error, setError] = useState(null);

    useEffect(() => {
      let alive = true;
      setMessages(null); setError(null);
      api(`/conversations/${encodeURIComponent(sessionId)}/messages`)
        .then((r) => { if (alive) setMessages(r.messages || []); })
        .catch((e) => { if (alive) setError(parseApiError(e)); });
      return () => { alive = false; };
    }, [sessionId]);

    return h("div", { className: "crm-thread" },
      h("div", { className: "crm-thread-head" },
        h(Button, { size: "sm", variant: "outline", onClick: onBack }, "← Back"),
        h("span", { className: "crm-thread-title" }, "Conversation")),
      error && h("div", { className: "crm-error" }, error),
      messages === null && !error && h("div", { className: "crm-muted" }, "Loading…"),
      messages && messages.length === 0 && h("div", { className: "crm-muted" }, "No messages."),
      messages && messages.map((m, i) =>
        h("div", { key: i, className: cn("crm-msg", `crm-msg--${m.role}`) },
          h("div", { className: "crm-msg-role" }, m.role),
          h("div", { className: "crm-msg-body" }, m.content || ""))));
  }

  // -------------------------------------------------------------------------
  // Conversations list for a contact
  // -------------------------------------------------------------------------
  function Conversations(props) {
    const { contactId } = props;
    const [convos, setConvos] = useState(null);
    const [openId, setOpenId] = useState(null);

    useEffect(() => {
      let alive = true;
      setConvos(null); setOpenId(null);
      api(`/contacts/${encodeURIComponent(contactId)}/conversations`)
        .then((r) => { if (alive) setConvos(r.conversations || []); })
        .catch(() => { if (alive) setConvos([]); });
      return () => { alive = false; };
    }, [contactId]);

    if (openId) return h(Thread, { sessionId: openId, onBack: () => setOpenId(null) });

    return h("div", { className: "crm-convos" },
      h("div", { className: "crm-section-title" }, "Conversations"),
      convos === null && h("div", { className: "crm-muted" }, "Loading…"),
      convos && convos.length === 0 &&
        h("div", { className: "crm-muted" }, "No linked conversations. Link a handle to attach history."),
      convos && convos.map((s) =>
        h("button", {
          key: s.session_id,
          className: "crm-convo-row",
          onClick: () => setOpenId(s.session_id),
        },
          h("span", { className: "crm-chip" }, s.platform),
          h("span", { className: "crm-convo-name" }, s.user_name || s.user_id),
          h("span", { className: "crm-muted crm-convo-when" }, timeAgo(s.updated_at)))));
  }

  // -------------------------------------------------------------------------
  // Contact detail / editor (right column)
  // -------------------------------------------------------------------------
  function ContactDetail(props) {
    const { contact, statuses, onSaved, onDeleted } = props;
    const [form, setForm] = useState(null);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState(null);

    useEffect(() => {
      setError(null);
      setForm({
        display_name: contact.display_name,
        status: contact.status,
        source: contact.source || "",
        notes: contact.notes || "",
        emails: (contact.emails || []).join(", "),
        tags: (contact.tags || []).join(", "),
        handles: contact.handles || [],
      });
    }, [contact.id]);

    const setField = (k, v) => setForm((f) => Object.assign({}, f, { [k]: v }));

    const splitList = (s) =>
      (s || "").split(",").map((x) => x.trim()).filter(Boolean);

    const save = useCallback(() => {
      if (!form) return;
      setSaving(true); setError(null);
      api(`/contacts/${encodeURIComponent(contact.id)}`, {
        method: "PATCH",
        body: JSON.stringify({
          display_name: form.display_name,
          status: form.status,
          source: form.source || null,
          notes: form.notes,
          emails: splitList(form.emails),
          tags: splitList(form.tags),
          handles: form.handles,
        }),
      })
        .then((c) => { setSaving(false); onSaved(c); })
        .catch((e) => { setSaving(false); setError(parseApiError(e)); });
    }, [form, contact.id, onSaved]);

    const remove = useCallback(() => {
      if (!window.confirm(`Delete contact "${contact.display_name}"? This does not delete any conversation history.`)) return;
      api(`/contacts/${encodeURIComponent(contact.id)}`, { method: "DELETE" })
        .then(() => onDeleted(contact.id))
        .catch((e) => setError(parseApiError(e)));
    }, [contact.id, contact.display_name, onDeleted]);

    const addHandle = () => {
      const platform = (window.prompt("Platform (e.g. telegram, whatsapp):") || "").trim();
      if (!platform) return;
      const user_id = (window.prompt(`${platform} user id:`) || "").trim();
      if (!user_id) return;
      setForm((f) => Object.assign({}, f, {
        handles: [...(f.handles || []), { platform: platform.toLowerCase(), user_id }],
      }));
    };
    const removeHandle = (i) =>
      setForm((f) => Object.assign({}, f, {
        handles: (f.handles || []).filter((_, idx) => idx !== i),
      }));

    if (!form) return null;

    return h("div", { className: "crm-detail" },
      h("div", { className: "crm-field" },
        h(Label, null, "Name"),
        h(Input, { value: form.display_name,
          onChange: (e) => setField("display_name", e.target.value) })),

      h("div", { className: "crm-field-row" },
        h("div", { className: "crm-field" },
          h(Label, null, "Status"),
          h(Select, {
            value: form.status,
            onValueChange: (v) => setField("status", v),
            onChange: (e) => setField("status", e && e.target ? e.target.value : e),
          }, (statuses || STATUS_ORDER).map((s) =>
            h(SelectOption, { key: s, value: s }, STATUS_LABEL[s] || s)))),
        h("div", { className: "crm-field" },
          h(Label, null, "Source"),
          h(Input, { value: form.source, placeholder: "email / manual / telegram …",
            onChange: (e) => setField("source", e.target.value) }))),

      h("div", { className: "crm-field" },
        h(Label, null, "Emails (comma-separated)"),
        h(Input, { value: form.emails,
          onChange: (e) => setField("emails", e.target.value) })),

      h("div", { className: "crm-field" },
        h(Label, null, "Tags (comma-separated)"),
        h(Input, { value: form.tags,
          onChange: (e) => setField("tags", e.target.value) })),

      h("div", { className: "crm-field" },
        h(Label, null, "Notes"),
        h("textarea", {
          className: "crm-textarea",
          value: form.notes,
          rows: 4,
          onChange: (e) => setField("notes", e.target.value),
        })),

      h("div", { className: "crm-field" },
        h("div", { className: "crm-handles-head" },
          h(Label, null, "Linked handles"),
          h(Button, { size: "sm", variant: "outline", onClick: addHandle }, "+ Link handle")),
        h("div", { className: "crm-chips" },
          (form.handles || []).length === 0
            ? h("span", { className: "crm-muted" }, "None")
            : form.handles.map((hd, i) =>
                h("span", { key: i, className: "crm-chip crm-chip--rm",
                  title: "Click to unlink", onClick: () => removeHandle(i) },
                  `${hd.platform}: ${hd.user_id} ✕`)))),

      error && h("div", { className: "crm-error" }, error),

      h("div", { className: "crm-actions" },
        h(Button, { onClick: save, disabled: saving }, saving ? "Saving…" : "Save"),
        h(Button, { variant: "outline", onClick: remove }, "Delete")),

      h(Conversations, { contactId: contact.id }));
  }

  // -------------------------------------------------------------------------
  // New contact form
  // -------------------------------------------------------------------------
  function NewContact(props) {
    const { statuses, prefill, onCreated, onCancel } = props;
    const [form, setForm] = useState({
      display_name: (prefill && prefill.display_name) || "",
      status: "lead",
      source: (prefill && prefill.source) || "",
      emails: "",
      tags: "",
      handles: (prefill && prefill.handles) || [],
    });
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState(null);
    const setField = (k, v) => setForm((f) => Object.assign({}, f, { [k]: v }));
    const splitList = (s) => (s || "").split(",").map((x) => x.trim()).filter(Boolean);

    const create = () => {
      if (!form.display_name.trim()) { setError("Name is required"); return; }
      setSaving(true); setError(null);
      api("/contacts", {
        method: "POST",
        body: JSON.stringify({
          display_name: form.display_name,
          status: form.status,
          source: form.source || null,
          emails: splitList(form.emails),
          tags: splitList(form.tags),
          handles: form.handles,
        }),
      })
        .then((c) => { setSaving(false); onCreated(c); })
        .catch((e) => { setSaving(false); setError(parseApiError(e)); });
    };

    return h(Card, { className: "crm-new" },
      h(CardContent, null,
        h("div", { className: "crm-section-title" }, "New contact"),
        h("div", { className: "crm-field" },
          h(Label, null, "Name"),
          h(Input, { value: form.display_name, autoFocus: true,
            onChange: (e) => setField("display_name", e.target.value) })),
        h("div", { className: "crm-field-row" },
          h("div", { className: "crm-field" },
            h(Label, null, "Status"),
            h(Select, {
              value: form.status,
              onValueChange: (v) => setField("status", v),
              onChange: (e) => setField("status", e && e.target ? e.target.value : e),
            }, (statuses || STATUS_ORDER).map((s) =>
              h(SelectOption, { key: s, value: s }, STATUS_LABEL[s] || s)))),
          h("div", { className: "crm-field" },
            h(Label, null, "Source"),
            h(Input, { value: form.source,
              onChange: (e) => setField("source", e.target.value) }))),
        h("div", { className: "crm-field" },
          h(Label, null, "Emails (comma-separated)"),
          h(Input, { value: form.emails,
            onChange: (e) => setField("emails", e.target.value) })),
        h("div", { className: "crm-field" },
          h(Label, null, "Tags (comma-separated)"),
          h(Input, { value: form.tags,
            onChange: (e) => setField("tags", e.target.value) })),
        (form.handles || []).length > 0 && h("div", { className: "crm-chips" },
          form.handles.map((hd, i) =>
            h("span", { key: i, className: "crm-chip" }, `${hd.platform}: ${hd.user_id}`))),
        error && h("div", { className: "crm-error" }, error),
        h("div", { className: "crm-actions" },
          h(Button, { onClick: create, disabled: saving }, saving ? "Creating…" : "Create"),
          h(Button, { variant: "outline", onClick: onCancel }, "Cancel"))));
  }

  // -------------------------------------------------------------------------
  // Unlinked conversations inbox
  // -------------------------------------------------------------------------
  function UnlinkedInbox(props) {
    const { onPrefillNew, onClose } = props;
    const [items, setItems] = useState(null);

    useEffect(() => {
      let alive = true;
      api("/unlinked")
        .then((r) => { if (alive) setItems(r.unlinked || []); })
        .catch(() => { if (alive) setItems([]); });
      return () => { alive = false; };
    }, []);

    return h(Card, { className: "crm-new" },
      h(CardContent, null,
        h("div", { className: "crm-handles-head" },
          h("div", { className: "crm-section-title" }, "Unlinked conversations"),
          h(Button, { size: "sm", variant: "outline", onClick: onClose }, "Close")),
        h("div", { className: "crm-muted" },
          "Direct-message senders not yet attached to a contact."),
        items === null && h("div", { className: "crm-muted" }, "Loading…"),
        items && items.length === 0 && h("div", { className: "crm-muted" }, "Nothing unlinked."),
        items && items.map((s) =>
          h("div", { key: `${s.platform}:${s.user_id}`, className: "crm-convo-row crm-unlinked-row" },
            h("span", { className: "crm-chip" }, s.platform),
            h("span", { className: "crm-convo-name" }, s.user_name || s.user_id),
            h("span", { className: "crm-muted crm-convo-when" }, timeAgo(s.updated_at)),
            h(Button, {
              size: "sm",
              onClick: () => onPrefillNew({
                display_name: s.user_name || s.user_id,
                source: s.platform,
                handles: [{ platform: s.platform, user_id: s.user_id }],
              }),
            }, "Create contact")))));
  }

  // -------------------------------------------------------------------------
  // Main page
  // -------------------------------------------------------------------------
  function CrmPage() {
    const [contacts, setContacts] = useState([]);
    const [total, setTotal] = useState(0);
    const [q, setQ] = useState("");
    const [statusFilter, setStatusFilter] = useState("");
    const [statuses, setStatuses] = useState(STATUS_ORDER);
    const [selectedId, setSelectedId] = useState(null);
    const [detail, setDetail] = useState(null);
    const [mode, setMode] = useState("view"); // view | new | unlinked
    const [prefill, setPrefill] = useState(null);
    const [listError, setListError] = useState(null);

    useEffect(() => {
      api("/meta").then((r) => setStatuses(r.statuses || STATUS_ORDER)).catch(() => {});
    }, []);

    const reload = useCallback(() => {
      const params = new URLSearchParams();
      if (q) params.set("q", q);
      if (statusFilter) params.set("status", statusFilter);
      setListError(null);
      api(`/contacts?${params.toString()}`)
        .then((r) => { setContacts(r.contacts || []); setTotal(r.total || 0); })
        .catch((e) => setListError(parseApiError(e)));
    }, [q, statusFilter]);

    useEffect(() => {
      const t = setTimeout(reload, 200); // debounce search
      return () => clearTimeout(t);
    }, [reload]);

    const openContact = useCallback((id) => {
      setMode("view"); setSelectedId(id); setDetail(null);
      api(`/contacts/${encodeURIComponent(id)}`)
        .then(setDetail)
        .catch(() => setDetail(null));
    }, []);

    const onSaved = useCallback((c) => { setDetail(c); reload(); }, [reload]);
    const onDeleted = useCallback((id) => {
      if (selectedId === id) { setSelectedId(null); setDetail(null); }
      reload();
    }, [selectedId, reload]);
    const onCreated = useCallback((c) => {
      setMode("view"); setPrefill(null); reload(); openContact(c.id);
    }, [reload, openContact]);

    let rightPane;
    if (mode === "new") {
      rightPane = h(NewContact, {
        statuses, prefill,
        onCreated,
        onCancel: () => { setMode("view"); setPrefill(null); },
      });
    } else if (mode === "unlinked") {
      rightPane = h(UnlinkedInbox, {
        onClose: () => setMode("view"),
        onPrefillNew: (pf) => { setPrefill(pf); setMode("new"); },
      });
    } else if (detail) {
      rightPane = h(ContactDetail, { contact: detail, statuses, onSaved, onDeleted });
    } else if (selectedId) {
      rightPane = h("div", { className: "crm-muted crm-detail" }, "Loading…");
    } else {
      rightPane = h("div", { className: "crm-placeholder" },
        "Select a contact, or create one.");
    }

    return h("div", { className: "crm-page" },
      h("div", { className: "crm-toolbar" },
        h(Input, {
          className: "crm-search",
          placeholder: "Search name, email, handle…",
          value: q,
          onChange: (e) => setQ(e.target.value),
        }),
        h(Select, {
          value: statusFilter,
          onValueChange: (v) => setStatusFilter(v),
          onChange: (e) => setStatusFilter(e && e.target ? e.target.value : e),
        },
          h(SelectOption, { value: "" }, "All statuses"),
          (statuses || STATUS_ORDER).map((s) =>
            h(SelectOption, { key: s, value: s }, STATUS_LABEL[s] || s))),
        h(Button, { size: "sm", onClick: () => { setPrefill(null); setMode("new"); } }, "+ New"),
        h(Button, { size: "sm", variant: "outline", onClick: () => setMode("unlinked") }, "Unlinked")),

      listError && h("div", { className: "crm-error" }, listError),

      h("div", { className: "crm-body" },
        h(Card, { className: "crm-left" },
          h(CardContent, null,
            h(ContactList, {
              contacts, total, selectedId,
              onSelect: openContact,
            }))),
        h("div", { className: "crm-right" }, rightPane)));
  }

  // -------------------------------------------------------------------------
  // Register
  // -------------------------------------------------------------------------
  if (window.__HERMES_PLUGINS__ && typeof window.__HERMES_PLUGINS__.register === "function") {
    window.__HERMES_PLUGINS__.register("crm", CrmPage);
  }
})();
