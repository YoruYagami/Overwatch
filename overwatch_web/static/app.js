// Overwatch Security Scanner - Frontend JavaScript

// Global state
let scans = [];
let selectedScans = new Set();
let pollInterval = null;
let isModifyMode = false;
let modifyingSlug = null;

// DOM Elements
const scanTableBody = document.getElementById('scan-table-body');
const selectAllCheckbox = document.getElementById('select-all');
const newScanBtn = document.getElementById('new-scan-btn');
const modifyScanBtn = document.getElementById('modify-scan-btn');
const deleteScanBtn = document.getElementById('delete-scan-btn');
const modal = document.getElementById('scan-modal');
const modalBackdrop = document.getElementById('modal-backdrop');
const modalClose = document.getElementById('modal-close');
const modalCancel = document.getElementById('modal-cancel');
const modalForm = document.getElementById('modal-form');
const modalTitle = document.getElementById('modal-title');
const modalProjectName = document.getElementById('modal-project-name');
const modalTargets = document.getElementById('modal-targets');
const modalSchedule = document.getElementById('modal-schedule');
const modalScheduleField = document.getElementById('modal-schedule-field');
const modalFeedback = document.getElementById('modal-feedback');
const flashMessage = document.getElementById('flash-message');
const themeToggle = document.getElementById('theme-toggle');
const targetsModal = document.getElementById('targets-modal');

// Proxy Elements
const proxyEnabled = document.getElementById('modal-proxy-enabled');
const proxySettings = document.getElementById('proxy-settings');
const proxyType = document.getElementById('modal-proxy-type');
const proxyHost = document.getElementById('modal-proxy-host');
const proxyPort = document.getElementById('modal-proxy-port');
const proxyUser = document.getElementById('modal-proxy-user');
const proxyPass = document.getElementById('modal-proxy-pass');

// Scan Options
const skipSubdomain = document.getElementById('modal-skip-subdomain');

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    loadScans();
    startPolling();
    initializeEventListeners();
    initializeTheme();
});

// Event Listeners
function initializeEventListeners() {
    selectAllCheckbox.addEventListener('change', handleSelectAll);
    newScanBtn.addEventListener('click', openNewScanModal);
    modifyScanBtn.addEventListener('click', handleModify);
    deleteScanBtn.addEventListener('click', handleDelete);
    modalClose.addEventListener('click', closeModal);
    modalCancel.addEventListener('click', closeModal);
    modalBackdrop.addEventListener('click', closeModal);
    themeToggle.addEventListener('click', toggleTheme);

    // Proxy configuration toggle
    proxyEnabled.addEventListener('change', () => {
        if (proxyEnabled.checked) {
            proxySettings.classList.remove('hidden');
        } else {
            proxySettings.classList.add('hidden');
        }
    });

    // Run mode buttons
    document.querySelectorAll('[data-run-mode]').forEach(btn => {
        btn.addEventListener('click', (e) => handleSubmit(e.target.dataset.runMode));
    });

    // Targets modal close
    document.querySelectorAll('.targets-modal-close').forEach(btn => {
        btn.addEventListener('click', () => {
            targetsModal.classList.add('hidden');
            modalBackdrop.classList.add('hidden');
        });
    });

    // Schedule button shows datetime field
    document.querySelector('[data-run-mode="schedule"]').addEventListener('click', () => {
        modalScheduleField.classList.remove('hidden');
    });
}

// Toggle Proxy Configuration Section
function toggleProxyConfig() {
    const section = document.getElementById('proxy-config-section');
    const icon = document.getElementById('proxy-toggle-icon');

    section.classList.toggle('hidden');
    icon.classList.toggle('rotated');
}

// Toggle Scan Options Section
function toggleScanOptions() {
    const section = document.getElementById('scan-options-section');
    const icon = document.getElementById('scan-options-toggle-icon');

    section.classList.toggle('hidden');
    icon.classList.toggle('rotated');
}

// Theme Management
function initializeTheme() {
    const savedTheme = localStorage.getItem('theme') || 'dark';
    document.body.setAttribute('data-theme', savedTheme);
    updateThemeIcon(savedTheme);
}

function toggleTheme() {
    const currentTheme = document.body.getAttribute('data-theme');
    const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
    document.body.setAttribute('data-theme', newTheme);
    localStorage.setItem('theme', newTheme);
    updateThemeIcon(newTheme);
}

function updateThemeIcon(theme) {
    const icon = themeToggle.querySelector('.theme-icon');
    icon.textContent = theme === 'dark' ? '☀️' : '🌙';
}

// API Calls
async function loadScans() {
    try {
        const response = await fetch('/api/scans');
        const data = await response.json();
        scans = data.scans || [];
        renderScans();
    } catch (error) {
        console.error('Failed to load scans:', error);
        showFlash('Failed to load scans. Please refresh the page.', 'error');
    }
}

async function createScan(scanData, runMode) {
    try {
        const response = await fetch('/api/scans', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(scanData)
        });

        const result = await response.json();

        if (!response.ok) {
            throw new Error(result.error || 'Failed to create scan');
        }

        showFlash(result.message || 'Scan created successfully', 'success');
        closeModal();
        loadScans();
        return result;
    } catch (error) {
        throw error;
    }
}

async function updateScan(slug, scanData) {
    try {
        const response = await fetch(`/api/scans/${slug}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(scanData)
        });

        const result = await response.json();

        if (!response.ok) {
            throw new Error(result.error || 'Failed to update scan');
        }

        showFlash(result.message || 'Scan updated successfully', 'success');
        closeModal();
        loadScans();
        return result;
    } catch (error) {
        throw error;
    }
}

async function deleteScan(slug) {
    try {
        const response = await fetch(`/api/scans/${slug}`, {
            method: 'DELETE'
        });

        const result = await response.json();

        if (!response.ok) {
            throw new Error(result.error || 'Failed to delete scan');
        }

        return result;
    } catch (error) {
        throw error;
    }
}

async function rescanProject(slug) {
    try {
        const response = await fetch(`/api/scans/${slug}/rescan`, {
            method: 'POST'
        });

        const result = await response.json();

        if (!response.ok) {
            throw new Error(result.error || 'Failed to start rescan');
        }

        showFlash(result.message || 'Rescan started', 'success');
        loadScans();
        return result;
    } catch (error) {
        showFlash(error.message, 'error');
    }
}

async function cancelScan(slug) {
    try {
        const response = await fetch(`/api/scans/${slug}/cancel`, {
            method: 'POST'
        });

        const result = await response.json();

        if (!response.ok) {
            throw new Error(result.error || 'Failed to cancel scan');
        }

        showFlash(result.message || 'Scan cancelled', 'info');
        loadScans();
        return result;
    } catch (error) {
        showFlash(error.message, 'error');
    }
}

// Rendering
function renderScans() {
    if (!scans || scans.length === 0) {
        scanTableBody.innerHTML = `
            <tr class="empty">
                <td colspan="9">
                    <div class="empty-state">
                        <div class="empty-icon">🔍</div>
                        <p>No scans yet. Click <strong>New Scan</strong> to get started.</p>
                    </div>
                </td>
            </tr>
        `;
        return;
    }

    scanTableBody.innerHTML = scans.map(scan => `
        <tr data-slug="${scan.slug}" ${selectedScans.has(scan.slug) ? 'class="selected"' : ''}>
            <td class="select-col">
                <input type="checkbox" class="scan-checkbox" data-slug="${scan.slug}"
                       ${selectedScans.has(scan.slug) ? 'checked' : ''}
                       ${scan.locked ? 'disabled' : ''}>
            </td>
            <td>${scan.index}</td>
            <td><strong>${escapeHtml(scan.name)}</strong></td>
            <td>
                <span class="targets-preview" onclick="showTargets('${scan.slug}')">
                    ${scan.targets_count} domain${scan.targets_count !== 1 ? 's' : ''}
                </span>
            </td>
            <td>
                ${renderProgress(scan.progress)}
            </td>
            <td>${formatDate(scan.ran_at) || 'Never'}</td>
            <td>${renderStatus(scan.status, scan.status_message)}</td>
            <td>
                ${scan.report ?
                    `<div style="display: flex; gap: 0.5rem; flex-wrap: wrap;">
                        <a href="${scan.report}" target="_blank" class="btn-secondary" style="padding: 0.5rem 1rem; text-decoration: none;">
                            📊 View Report
                        </a>
                        <a href="/analyzer/${scan.slug}/${scan.run_id}" class="btn-secondary" style="padding: 0.5rem 1rem; text-decoration: none;">
                            🔍 Analyze
                        </a>
                    </div>` :
                    '<span style="color: var(--text-secondary);">No report</span>'
                }
            </td>
            <td class="actions-col">
                ${renderActions(scan)}
            </td>
        </tr>
    `).join('');

    // Attach checkbox listeners
    document.querySelectorAll('.scan-checkbox').forEach(cb => {
        cb.addEventListener('change', handleCheckboxChange);
    });

    updateButtonStates();
}

function renderStatus(status, message) {
    const statusMap = {
        'running': 'running',
        'succeeded': 'succeeded',
        'failed': 'failed',
        'queued': 'queued',
        'scheduled': 'scheduled',
        'never': 'never'
    };

    const badgeClass = statusMap[status] || 'never';
    return `<span class="status-badge ${badgeClass}" title="${escapeHtml(message)}">${status}</span>`;
}

function renderProgress(progress) {
    if (!progress || progress.percent === 0) {
        return '<div class="progress-container"><div class="progress-bar"><div class="progress-fill" style="width: 0%"></div></div><div class="progress-label">Not started</div></div>';
    }

    return `
        <div class="progress-container">
            <div class="progress-bar">
                <div class="progress-fill" style="width: ${progress.percent}%"></div>
            </div>
            <div class="progress-label">
                ${progress.step}/${progress.total} - ${escapeHtml(progress.label || 'Processing')}
            </div>
        </div>
    `;
}

function renderActions(scan) {
    let actions = '';

    if (scan.is_running) {
        actions += `<button class="action-btn" onclick="cancelScan('${scan.slug}')" title="Cancel">⏹️</button>`;
    } else if (!scan.locked) {
        actions += `<button class="action-btn" onclick="rescanProject('${scan.slug}')" title="Rescan">🔄</button>`;
    }

    if (scan.report) {
        actions += `<a href="/projects/${scan.slug}/runs/${scan.run_id}/download/json" class="action-btn" title="Download JSON" download>📥</a>`;
    }

    return actions || '<span style="color: var(--text-secondary);">—</span>';
}

function showTargets(slug) {
    const scan = scans.find(s => s.slug === slug);
    if (!scan) return;

    const targetsList = document.getElementById('targets-list');
    const targets = scan.targets.split('\n').filter(t => t.trim());

    targetsList.innerHTML = targets.map(target =>
        `<div>${escapeHtml(target)}</div>`
    ).join('');

    targetsModal.classList.remove('hidden');
    modalBackdrop.classList.remove('hidden');
}

// Modal Handlers
function openNewScanModal() {
    isModifyMode = false;
    modifyingSlug = null;
    modalTitle.textContent = 'New Scan';
    modalForm.reset();
    modalScheduleField.classList.add('hidden');
    modalFeedback.classList.remove('show');
    modal.classList.remove('hidden');
    modalBackdrop.classList.remove('hidden');
}

function openModifyScanModal(slug) {
    const scan = scans.find(s => s.slug === slug);
    if (!scan) return;

    isModifyMode = true;
    modifyingSlug = slug;
    modalTitle.textContent = 'Modify Scan';
    modalProjectName.value = scan.name;
    modalTargets.value = scan.targets;
    modalScheduleField.classList.add('hidden');
    modalFeedback.classList.remove('show');
    modal.classList.remove('hidden');
    modalBackdrop.classList.remove('hidden');
}

function closeModal() {
    modal.classList.add('hidden');
    targetsModal.classList.add('hidden');
    modalBackdrop.classList.add('hidden');
    modalForm.reset();
    modalFeedback.classList.remove('show');
    isModifyMode = false;
    modifyingSlug = null;
}

async function handleSubmit(runMode) {
    const projectName = modalProjectName.value.trim();
    const targets = modalTargets.value.trim();
    let scheduledFor = null;

    if (!projectName || !targets) {
        showModalFeedback('Please fill in all required fields', 'error');
        return;
    }

    // Validate proxy settings if enabled
    if (proxyEnabled.checked) {
        const pHost = proxyHost.value.trim();
        const pPort = proxyPort.value.trim();
        if (!pHost || !pPort) {
            showModalFeedback('Proxy host and port are required when proxy is enabled', 'error');
            return;
        }
    }

    if (runMode === 'schedule') {
        scheduledFor = modalSchedule.value;
        if (!scheduledFor) {
            showModalFeedback('Please select a schedule time', 'error');
            modalScheduleField.classList.remove('hidden');
            return;
        }
        // Convert to ISO format
        scheduledFor = new Date(scheduledFor).toISOString();
    }

    const scanData = {
        project_name: projectName,
        targets: targets,
        start_mode: runMode,
        scheduled_for: scheduledFor,
        proxy_enabled: proxyEnabled.checked,
        proxy_type: proxyType.value,
        proxy_host: proxyHost.value.trim(),
        proxy_port: proxyPort.value.trim(),
        proxy_user: proxyUser.value.trim(),
        proxy_pass: proxyPass.value.trim(),
        skip_subdomain_enum: skipSubdomain.checked
    };

    try {
        if (isModifyMode && modifyingSlug) {
            await updateScan(modifyingSlug, scanData);
        } else {
            await createScan(scanData, runMode);
        }
    } catch (error) {
        showModalFeedback(error.message, 'error');
    }
}

// Selection Handlers
function handleSelectAll(e) {
    const checked = e.target.checked;
    selectedScans.clear();

    if (checked) {
        scans.forEach(scan => {
            if (!scan.locked) {
                selectedScans.add(scan.slug);
            }
        });
    }

    renderScans();
}

function handleCheckboxChange(e) {
    const slug = e.target.dataset.slug;
    if (e.target.checked) {
        selectedScans.add(slug);
    } else {
        selectedScans.delete(slug);
        selectAllCheckbox.checked = false;
    }
    renderScans();
}

function updateButtonStates() {
    const hasSelection = selectedScans.size > 0;
    const singleSelection = selectedScans.size === 1;

    modifyScanBtn.disabled = !singleSelection;
    deleteScanBtn.disabled = !hasSelection;
}

function handleModify() {
    if (selectedScans.size !== 1) return;
    const slug = Array.from(selectedScans)[0];
    openModifyScanModal(slug);
}

async function handleDelete() {
    if (selectedScans.size === 0) return;

    const count = selectedScans.size;
    if (!confirm(`Are you sure you want to delete ${count} scan${count > 1 ? 's' : ''}? This action cannot be undone.`)) {
        return;
    }

    const slugsToDelete = Array.from(selectedScans);
    let successCount = 0;
    let errorCount = 0;

    for (const slug of slugsToDelete) {
        try {
            await deleteScan(slug);
            successCount++;
            selectedScans.delete(slug);
        } catch (error) {
            console.error(`Failed to delete ${slug}:`, error);
            errorCount++;
        }
    }

    if (successCount > 0) {
        showFlash(`Successfully deleted ${successCount} scan${successCount > 1 ? 's' : ''}`, 'success');
    }
    if (errorCount > 0) {
        showFlash(`Failed to delete ${errorCount} scan${errorCount > 1 ? 's' : ''}`, 'error');
    }

    loadScans();
}

// Polling
function startPolling() {
    // Poll every 3 seconds
    pollInterval = setInterval(loadScans, 3000);
}

function stopPolling() {
    if (pollInterval) {
        clearInterval(pollInterval);
        pollInterval = null;
    }
}

// Utility Functions
function showFlash(message, type = 'info') {
    flashMessage.textContent = message;
    flashMessage.className = `flash ${type} show`;
    setTimeout(() => {
        flashMessage.classList.remove('show');
    }, 5000);
}

function showModalFeedback(message, type = 'error') {
    modalFeedback.textContent = message;
    modalFeedback.className = `feedback ${type} show`;
}

function formatDate(isoString) {
    if (!isoString) return null;
    try {
        const date = new Date(isoString);
        return date.toLocaleString();
    } catch {
        return isoString;
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Cleanup on page unload
window.addEventListener('beforeunload', stopPolling);
