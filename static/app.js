document.addEventListener('DOMContentLoaded', () => {
    // DOM Elements
    const installBtn = document.getElementById('btn-install');
    const refreshBtn = document.getElementById('btn-refresh');
    const installPathInput = document.getElementById('install-path');
    const logList = document.getElementById('log-list');
    const liveIndicator = document.getElementById('live-indicator');
    const appsContainer = document.getElementById('apps-container');
    const themeToggle = document.getElementById('theme-toggle');

    // Theme Toggle Logic
    let isDarkMode = true;
    themeToggle.addEventListener('click', () => {
        isDarkMode = !isDarkMode;
        document.body.setAttribute('data-theme', isDarkMode ? 'dark' : 'light');
        themeToggle.innerHTML = isDarkMode ? '<i class="fa-solid fa-moon"></i>' : '<i class="fa-solid fa-sun"></i>';
    });

    // Helper: Add log to the window
    function appendLog(msg, type = "normal") {
        const li = document.createElement('li');
        li.className = `log-entry ${type}`;

        const timeSpan = document.createElement('span');
        timeSpan.className = 'time';
        const now = new Date();
        timeSpan.innerText = `[${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}:${now.getSeconds().toString().padStart(2, '0')}]`;

        const msgSpan = document.createElement('span');
        msgSpan.className = 'msg';
        msgSpan.innerText = ` ${msg}`;

        li.appendChild(timeSpan);
        li.appendChild(msgSpan);

        logList.appendChild(li);

        // Ensure scroll to bottom
        setTimeout(() => {
            logList.scrollTop = logList.scrollHeight;
        }, 10);
    }

    // Connect Server-Sent Events for logs
    function startLogStream() {
        const evtSource = new EventSource("/api/install/status");

        evtSource.onmessage = function (event) {
            if (event.data === "ping") return;
            liveIndicator.classList.add('active');
            appendLog(event.data);

            // clear indicator animation randomly to simulate data packets arriving
            clearTimeout(window.indicatorTimeout);
            window.indicatorTimeout = setTimeout(() => {
                liveIndicator.classList.remove('active');
            }, 500);

            // Re-fetch apps list when installations are complete
            if (event.data.includes("All installations complete!") || event.data.includes("installed successfully ✓")) {
                setTimeout(fetchInstalledApps, 1000);
            }
        };

        evtSource.onerror = function (err) {
            liveIndicator.classList.remove('active');
        };
    }

    // Start SSE listener
    startLogStream();

    // Fetch and Display Installed Apps
    async function fetchInstalledApps() {
        try {
            refreshBtn.querySelector('i').classList.add('fa-spin');

            const response = await fetch('/api/apps');
            const data = await response.json();

            appsContainer.innerHTML = ''; // clear loading state

            if (!data.apps || data.apps.length === 0) {
                appsContainer.innerHTML = `
                    <div class="empty-state">
                        <i class="fa-solid fa-box-open"></i>
                        <p>No apps installed yet.</p>
                    </div>
                `;
                return;
            }

            data.apps.forEach(app => {
                const card = document.createElement('div');
                card.className = 'app-card';
                card.innerHTML = `
                    <div class="app-info">
                        <div class="app-icon">
                            <i class="fa-solid fa-cube"></i>
                        </div>
                        <div class="app-details">
                            <h4>${app.name}</h4>
                            <p>v${app.version}</p>
                        </div>
                    </div>
                    <button class="btn-danger btn-uninstall" data-app="${app.name}">
                        <i class="fa-solid fa-trash"></i> Uninstall
                    </button>
                `;
                appsContainer.appendChild(card);
            });

            // Attach uninstall event listeners
            document.querySelectorAll('.btn-uninstall').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    const appName = e.target.closest('button').dataset.app;
                    uninstallApp(appName);
                });
            });

        } catch (error) {
            console.error("Failed to fetch apps", error);
            appsContainer.innerHTML = `
                <div class="empty-state" style="color: var(--danger)">
                    <i class="fa-solid fa-triangle-exclamation"></i>
                    <p>Failed to load apps!</p>
                </div>
            `;
        } finally {
            setTimeout(() => {
                refreshBtn.querySelector('i').classList.remove('fa-spin');
            }, 500);
        }
    }

    // Call API to install
    async function intitiateInstall() {
        const path = installPathInput.value.trim();
        if (!path) {
            alert('Please provide a valid path.');
            installPathInput.focus();
            return;
        }

        const icon = installBtn.querySelector('i');
        icon.className = 'fa-solid fa-circle-notch fa-spin';
        installBtn.disabled = true;

        try {
            const resp = await fetch('/api/install', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: path })
            });
            const result = await resp.json();
            appendLog(`Requested install for: ${path}`, 'system');
            installPathInput.value = '';
        } catch (error) {
            appendLog(`Error requesting install: ${error.message}`, 'system');
        } finally {
            icon.className = 'fa-solid fa-arrow-right';
            installBtn.disabled = false;
        }
    }

    // Call API to uninstall
    async function uninstallApp(appName) {
        if (!confirm(`Are you sure you want to uninstall ${appName}?`)) return;

        appendLog(`Initiating uninstall for: ${appName}...`, 'system');

        try {
            const resp = await fetch('/api/uninstall', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ app: appName })
            });

            const result = await resp.json();

            if (resp.ok) {
                appendLog(result.message || `Successfully uninstalled ${appName}.`, 'system');
                fetchInstalledApps();
            } else {
                alert(`Error: ${result.error}`);
                appendLog(`Failed to uninstall ${appName}: ${result.error}`, 'system');
            }
        } catch (error) {
            console.error(error);
            appendLog(`Uninstall Error: ${error.message}`, 'system');
        }
    }

    // Event Listeners
    refreshBtn.addEventListener('click', fetchInstalledApps);
    installBtn.addEventListener('click', intitiateInstall);
    installPathInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') intitiateInstall();
    });

    // Initial load
    fetchInstalledApps();
});
