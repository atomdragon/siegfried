/**
 * kwin_focus_watcher.js
 *
 * Event-driven window focus watcher for KDE Plasma 6 (KWin) under Wayland.
 *
 * ARCHITECTURAL PRINCIPLE:
 * "The determinist core validates, calculates and executes."
 * This script provides event-driven activity signals to Siegfried daemon.
 *
 * STRICT PRIVACY CONTRACT:
 * - NO window titles, window captions, URLs, document names, or process arguments.
 * - Extracts ONLY normalized application identifier (desktopFileName or resourceClass).
 * - ZERO POLLING: Subscribes strictly to workspace.windowActivated signals.
 * - Fire-and-forget D-Bus call to org.siegfried.FocusWatcher on the session bus.
 * - Graceful degradation: Never blocks compositor thread or crashes KWin.
 */

(function () {
    "use strict";


    /**
     * Sanitize application identifier strictly.
     * Enforces alphanumeric + [._-] characters, lowercased, max 64 chars.
     * Rejects paths, spaces, query strings, URLs, or special characters.
     */
    function sanitizeAppId(raw) {
        if (!raw || typeof raw !== "string") {
            return "";
        }
        var clean = raw.trim().toLowerCase();
        // Remove trailing .desktop if present
        if (clean.length > 8 && clean.lastIndexOf(".desktop") === clean.length - 8) {
            clean = clean.substring(0, clean.length - 8);
        }
        // If raw string contains path separators, slashes, or query chars, discard
        if (clean.indexOf("/") !== -1 || clean.indexOf("\\") !== -1 || clean.indexOf("?") !== -1) {
            return "";
        }
        // Whitelist safe identifier characters only
        if (!/^[a-z0-9_.-]+$/.test(clean) || clean.length > 64) {
            return "";
        }
        return clean;
    }

    /**
     * Extract application identifier from window object without touching window title/caption.
     */
    function getAppIdFromWindow(window) {
        if (!window) {
            return "";
        }
        // In KWin 6 (Wayland), desktopFileName or resourceClass identifies the application
        var desk = window.desktopFileName;
        if (desk && typeof desk === "string" && desk.length > 0) {
            var sanitizedDesk = sanitizeAppId(desk);
            if (sanitizedDesk.length > 0) {
                return sanitizedDesk;
            }
        }

        var resClass = window.resourceClass;
        if (resClass && typeof resClass === "string" && resClass.length > 0) {
            var sanitizedRes = sanitizeAppId(resClass);
            if (sanitizedRes.length > 0) {
                return sanitizedRes;
            }
        }

        return "";
    }

    /**
     * Emit window change event to Siegfried daemon via session D-Bus.
     */
    function emitFocusTransition(appId) {
        // Python deduplicates within a focus epoch. Always deliver native events:
        // the same app must be able to recover after lock/unlock or suspend/resume.
        try {
            // Asynchronous fire-and-forget D-Bus call
            callDBus(
                "org.siegfried.FocusWatcher",
                "/FocusWatcher",
                "org.siegfried.FocusWatcher",
                "WindowChanged",
                appId
            );
        } catch (err) {
            // Never throw inside KWin callback
            console.warn("siegfried_focus_watcher: D-Bus delivery error");
        }
    }

    /**
     * Handler invoked when active window changes in KWin compositor.
     */
    function onWindowActivated(window) {
        var appId = getAppIdFromWindow(window);
        emitFocusTransition(appId);
    }

    // Connect to KWin compositor event signal
    if (typeof workspace !== "undefined" && workspace && workspace.windowActivated) {
        workspace.windowActivated.connect(onWindowActivated);

        // Sample initial active window on script startup if present
        emitFocusTransition(getAppIdFromWindow(workspace.activeWindow));
        console.info("siegfried_focus_watcher: Connected to workspace.windowActivated (Wayland event-driven)");
    } else {
        console.warn("siegfried_focus_watcher: workspace.windowActivated not available");
    }
})();
