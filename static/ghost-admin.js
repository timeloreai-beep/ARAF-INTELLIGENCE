(() => {
    "use strict";

    const tokenKey = "ARAF_GHOST_ADMIN_TOKEN";
    const tokenTtlKey = "ARAF_GHOST_ADMIN_EXPIRES";
    const login = document.getElementById("login");
    const dashboard = document.getElementById("dashboard");
    const statusEl = document.getElementById("page-status");
    const healthEl = document.getElementById("health-feed");
    const scannerEl = document.getElementById("scanner-feed");
    const usersBody = document.getElementById("users-table-body");
    let token = sessionStorage.getItem(tokenKey) || "";

    function showStatus(message, element = statusEl) {
        element.textContent = message;
    }

    function showAuthenticated(authenticated) {
        login.hidden = authenticated;
        dashboard.hidden = !authenticated;
        document.getElementById("logout").hidden = !authenticated;
    }

    async function api(path, options = {}) {
        const headers = { ...(options.headers || {}) };
        if (token) headers.Authorization = `Bearer ${token}`;
        if (options.body !== undefined) headers["Content-Type"] = "application/json";
        const response = await fetch(path, {
            method: options.method || "GET",
            headers,
            body: options.body === undefined ? undefined : JSON.stringify(options.body),
            cache: "no-store"
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            const detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data);
            throw new Error(detail || `Request failed (${response.status})`);
        }
        return data;
    }

    function renderEntries(element, entries, valueLabel = "status") {
        element.replaceChildren();
        Object.entries(entries || {}).forEach(([name, value]) => {
            const row = document.createElement("div");
            row.className = "service";
            const label = document.createElement("span");
            label.textContent = name.replaceAll("_", " ");
            const state = document.createElement("span");
            const stateValue = typeof value === "object" ? value[valueLabel] || JSON.stringify(value) : value;
            state.textContent = String(stateValue);
            const text = String(stateValue).toLowerCase();
            state.className = text.includes("healthy") || text.includes("configured") ? "healthy" : (text.includes("error") ? "error" : "pending");
            row.append(label, state);
            element.append(row);
        });
    }

    async function loadControl() {
        const data = await api("/admin/ghost/control");
        const status = document.getElementById("control-state");
        status.textContent = `Auto-trade: ${data.auto_trade_enabled ? "ON" : "OFF"} • Global kill switch: ${data.global_kill_switch ? "ACTIVE" : "clear"} • Live trading: ${data.live_trading_enabled ? "ON" : "OFF"}`;
        document.getElementById("risk-limits").textContent =
            `Hard caps: ${data.limits.max_daily_trades} trades/day • ${data.limits.max_per_trade_risk_pct}% per trade • ₹${Number(data.limits.max_daily_loss_amount).toLocaleString("en-IN")} daily loss`;
    }

    async function setAutoTrade(enabled) {
        const data = await api("/admin/ghost/control", {
            method: "POST",
            body: { enabled, reason: enabled ? "ADMIN_START" : "ADMIN_STOP" }
        });
        showStatus(`Auto-trade ${data.auto_trade_enabled ? "started" : "stopped"}; global kill switch is ${data.global_kill_switch ? "active" : "clear"}.`);
        await loadControl();
    }

    async function setGlobalKillSwitch(active) {
        const data = await api("/admin/ghost/control", {
            method: "POST",
            body: {
                enabled: active ? false : undefined,
                global_kill_switch: active,
                reason: active ? "ADMIN_GLOBAL_KILL" : "ADMIN_GLOBAL_KILL_RELEASE"
            }
        });
        showStatus(`Global kill switch ${data.global_kill_switch ? "activated" : "released"}; auto-trade remains ${data.auto_trade_enabled ? "on" : "off"}.`);
        await loadControl();
    }

    async function resetRiskLock() {
        const target = document.getElementById("risk-reset-target").value.trim();
        if (!target) throw new Error("Enter the user's ID before resetting the risk lock.");
        if (!window.confirm(`Reset the 24-hour risk lock and loss watermark for ${target}?`)) return;
        await api(`/admin/ghost/risk/reset?target=${encodeURIComponent(target)}`, {
            method: "POST"
        });
        showStatus(`Risk lock and loss watermark reset for ${target}.`);
    }

    async function loadHealth() {
        healthEl.textContent = "Checking configured services…";
        const data = await api("/admin/ghost/health");
        renderEntries(healthEl, Object.fromEntries(data.services.map(item => [item.name, item.status])));
        renderEntries(document.getElementById("news-health"), data.news_providers);
    }

    async function loadScanner() {
        scannerEl.textContent = "Loading stored scan snapshots…";
        const data = await api("/admin/ghost/scanner?limit=20");
        scannerEl.replaceChildren();
        if (!data.scans.length) {
            scannerEl.textContent = `No scan snapshots (${data.status}). Run an India scan to populate this feed.`;
            return;
        }
        data.scans.forEach(scan => {
            const card = document.createElement("article");
            card.className = "service";
            const heading = document.createElement("strong");
            heading.textContent = `${scan.market} • ${scan.count} candidates • ${scan.created_at || ""}`;
            const symbols = document.createElement("p");
            symbols.textContent = (scan.items || []).map(item => `${item.symbol || item.name || "?"} ${item.change_pct ?? ""}%`).join(" · ");
            card.append(heading, symbols);
            scannerEl.append(card);
        });
    }

    function selectTab(name) {
        document.querySelectorAll("[data-admin-tab]").forEach(button => {
            const selected = button.dataset.adminTab === name;
            button.setAttribute("aria-selected", String(selected));
        });
        document.querySelectorAll(".admin-tab").forEach(panel => {
            panel.hidden = panel.id !== `admin-tab-${name}`;
        });
    }

    async function loadUsers() {
        usersBody.replaceChildren();
        const loadingRow = document.createElement("tr");
        const loadingCell = document.createElement("td");
        loadingCell.colSpan = 6;
        loadingCell.textContent = "Loading users…";
        loadingRow.append(loadingCell);
        usersBody.append(loadingRow);
        const data = await api("/admin/ghost/users?limit=200");
        usersBody.replaceChildren();
        if (!data.users.length) {
            const emptyRow = document.createElement("tr");
            const emptyCell = document.createElement("td");
            emptyCell.colSpan = 6;
            emptyCell.textContent = "No users are stored in Supabase.";
            emptyRow.append(emptyCell);
            usersBody.append(emptyRow);
            return;
        }
        data.users.forEach(user => {
            const row = document.createElement("tr");
            [
                user.user_name,
                user.email,
                user.subscription_status.replaceAll("_", " "),
                user.expires_at ? new Date(user.expires_at).toLocaleDateString("en-IN") : "—",
                user.kyc_status
            ].forEach(value => {
                const cell = document.createElement("td");
                cell.textContent = value || "—";
                row.append(cell);
            });

            const actionsCell = document.createElement("td");
            const actions = document.createElement("div");
            actions.className = "user-actions";
            const approve = document.createElement("button");
            approve.type = "button";
            approve.textContent = "Approve KYC";
            approve.disabled = ["APPROVED", "VERIFIED_COMPLIANT"].includes(user.kyc_status);
            approve.addEventListener("click", () => updateUser(
                user, "/kyc/approve", {}, "KYC approved"
            ));

            const vip = document.createElement("button");
            vip.type = "button";
            vip.textContent = user.is_vip_free ? "Remove Free VIP" : "Make Free/VIP";
            vip.addEventListener("click", () => updateUser(
                user, "/vip", { is_vip_free: !user.is_vip_free },
                user.is_vip_free ? "Free VIP removed" : "Free VIP enabled"
            ));

            const expiry = document.createElement("input");
            expiry.type = "date";
            expiry.setAttribute("aria-label", `Subscription expiry for ${user.user_name}`);
            const todayIndia = new Intl.DateTimeFormat("en-CA", {
                timeZone: "Asia/Kolkata"
            }).format(new Date());
            expiry.min = todayIndia;
            const existingExpiry = user.expires_at
                ? new Intl.DateTimeFormat("en-CA", {
                    timeZone: "Asia/Kolkata"
                }).format(new Date(user.expires_at))
                : "";
            expiry.value = existingExpiry >= todayIndia ? existingExpiry : todayIndia;
            const extend = document.createElement("button");
            extend.type = "button";
            extend.textContent = "Extend Subscription";
            extend.addEventListener("click", () => {
                if (!expiry.value) {
                    showStatus("Choose a subscription expiry date.", document.getElementById("users-status"));
                    return;
                }
                updateUser(
                    user, "/subscription", { expires_on: expiry.value },
                    "Subscription expiry updated"
                );
            });

            const customMonths = document.createElement("input");
            customMonths.type = "number";
            customMonths.min = "1";
            customMonths.max = "12";
            customMonths.step = "1";
            customMonths.value = "1";
            customMonths.setAttribute("aria-label", `Custom subscription months for ${user.user_name}`);
            const customActivate = document.createElement("button");
            customActivate.type = "button";
            customActivate.textContent = "Activate Custom Months";
            customActivate.addEventListener("click", () => {
                const months = Number.parseInt(customMonths.value, 10);
                if (!Number.isInteger(months) || months < 1 || months > 12) {
                    showStatus("Months must be a whole number from 1 to 12.", document.getElementById("users-status"));
                    return;
                }
                if (!window.confirm(
                    `Confirm ${months}-month activation for ${user.user_name}? Verify payment or confirm this admin override first.`
                )) return;
                updateUser(
                    user,
                    "/subscription/extend",
                    { months },
                    `Subscription activated for ${months} month${months === 1 ? "" : "s"}`
                );
            });
            actions.append(customMonths, customActivate);

            const access = document.createElement("button");
            access.type = "button";
            access.className = user.is_active ? "danger" : "safe";
            access.textContent = user.is_active ? "Revoke Access" : "Restore Access";
            access.addEventListener("click", () => {
                if (user.is_active && !window.confirm(`Revoke access for ${user.user_name}?`)) return;
                updateUser(
                    user, "/access", { is_active: !user.is_active },
                    user.is_active ? "User access revoked" : "User access restored"
                );
            });
            actions.prepend(approve, vip, expiry, extend);
            actions.append(access);
            actionsCell.append(actions);
            row.append(actionsCell);
            usersBody.append(row);
        });
    }

    async function updateUser(user, route, body, successMessage) {
        try {
            await api(`/admin/ghost/users/${encodeURIComponent(user.user_id)}${route}`, {
                method: "POST",
                body
            });
            showStatus(`${successMessage}: ${user.user_name}.`, document.getElementById("users-status"));
            await loadUsers();
        } catch (error) {
            showStatus(error.message, document.getElementById("users-status"));
        }
    }

    async function loadConfiguration() {
        const data = await api("/admin/ghost/configuration");
        document.getElementById("global-monthly-fee").value = data.global_monthly_fee_inr;
    }

    document.querySelectorAll("[data-admin-tab]").forEach(button => {
        button.addEventListener("click", () => selectTab(button.dataset.adminTab));
    });

    document.getElementById("system-configuration-form").addEventListener("submit", async event => {
        event.preventDefault();
        const input = document.getElementById("global-monthly-fee");
        try {
            const data = await api("/admin/ghost/configuration", {
                method: "POST",
                body: { global_monthly_fee_inr: Number(input.value) }
            });
            showStatus(
                `Global monthly fee saved: ₹${Number(data.global_monthly_fee_inr).toLocaleString("en-IN")}.`,
                document.getElementById("configuration-status")
            );
        } catch (error) {
            showStatus(error.message, document.getElementById("configuration-status"));
        }
    });

    document.getElementById("login-form").addEventListener("submit", async event => {
        event.preventDefault();
        const passwordInput = document.getElementById("admin-password");
        try {
            const data = await api("/admin/ghost/login", {
                method: "POST",
                body: { password: passwordInput.value }
            });
            token = data.access_token;
            sessionStorage.setItem(tokenKey, token);
            sessionStorage.setItem(tokenTtlKey, String(Date.now() + data.expires_in * 1000));
            passwordInput.value = "";
            showAuthenticated(true);
            await Promise.all([loadControl(), loadHealth(), loadScanner(), loadUsers(), loadConfiguration()]);
        } catch (error) {
            passwordInput.value = "";
            showStatus(error.message);
        }
    });

    document.getElementById("logout").addEventListener("click", () => {
        token = "";
        sessionStorage.removeItem(tokenKey);
        sessionStorage.removeItem(tokenTtlKey);
        showAuthenticated(false);
    });
    document.getElementById("auto-start").addEventListener("click", () => setAutoTrade(true).catch(error => showStatus(error.message)));
    document.getElementById("auto-stop").addEventListener("click", () => setAutoTrade(false).catch(error => showStatus(error.message)));
    document.getElementById("kill-activate").addEventListener("click", () => setGlobalKillSwitch(true).catch(error => showStatus(error.message)));
    document.getElementById("kill-release").addEventListener("click", () => setGlobalKillSwitch(false).catch(error => showStatus(error.message)));
    document.getElementById("risk-reset").addEventListener("click", () => resetRiskLock().catch(error => showStatus(error.message)));
    document.getElementById("refresh-health").addEventListener("click", () => loadHealth().catch(error => showStatus(error.message)));
    document.getElementById("refresh-scanner").addEventListener("click", () => loadScanner().catch(error => showStatus(error.message)));
    document.getElementById("refresh-users").addEventListener("click", () => loadUsers().catch(error => showStatus(error.message, document.getElementById("users-status"))));
    document.getElementById("scan-now").addEventListener("click", async () => {
        try {
            showStatus("Running the configured India universe scan…");
            const response = await api("/scanner/india", {
                method: "POST",
                body: { exchange: "ALL", eligible_only: true, limit: 5000 }
            });
            showStatus(`Scan complete: ${response.count} eligible candidates.`);
            await loadScanner();
        } catch (error) {
            showStatus(error.message);
        }
    });
    document.getElementById("manual-order-form").addEventListener("submit", async event => {
        event.preventDefault();
        try {
            const symbol = document.getElementById("manual-symbol").value.trim().toUpperCase();
            const side = document.getElementById("manual-side").value;
            const result = await api("/orders/place", {
                method: "POST",
                body: {
                    symbol,
                    exchange: document.getElementById("manual-exchange").value,
                    side,
                    quantity: Number(document.getElementById("manual-quantity").value),
                    entry_price: Number(document.getElementById("manual-entry").value),
                    stop_loss: Number(document.getElementById("manual-stop").value),
                    take_profit: Number(document.getElementById("manual-target").value) || null,
                    operation: "ENTRY",
                    mode: "PAPER",
                    is_auto_trade: false,
                    client_order_id: `GHOST_${Date.now()}_${crypto.getRandomValues(new Uint32Array(1))[0]}`
                }
            });
            showStatus(`Paper order ${result.state}: ${result.symbol || symbol} ${side}; risk checks passed.`, document.getElementById("manual-result"));
            await loadControl();
        } catch (error) {
            showStatus(error.message, document.getElementById("manual-result"));
        }
    });

    if (token && Number(sessionStorage.getItem(tokenTtlKey) || 0) > Date.now()) {
        showAuthenticated(true);
        Promise.all([loadControl(), loadHealth(), loadScanner(), loadUsers(), loadConfiguration()]).catch(error => showStatus(error.message));
        window.setInterval(() => loadHealth().catch(error => showStatus(error.message)), 30000);
        window.setInterval(() => loadScanner().catch(error => showStatus(error.message)), 10000);
    } else {
        token = "";
        sessionStorage.removeItem(tokenKey);
        sessionStorage.removeItem(tokenTtlKey);
        showAuthenticated(false);
    }
})();
