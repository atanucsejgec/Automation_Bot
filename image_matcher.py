"""
image_matcher.py — Template-based UI element detection using OpenCV.

Finds UI elements (buttons, menus, resource bars, etc.) on screen by
matching a reference screenshot against a live screen capture.  Useful for:
  • Detecting when the "Attack" button is visible
  • Finding troop icons to deploy
  • Verifying the battle-end screen appeared
  • Waiting for a specific UI state before continuing

Usage:
    from image_matcher import ImageMatcher
    matcher = ImageMatcher(config)

    # Find a template on screen
    result = matcher.find("templates/attack_button.png")
    if result:
        x, y, confidence = result
        pyautogui.click(x, y)

    # Wait until a template appears (or timeout)
    pos = matcher.wait_for("templates/end_battle.png", timeout=30)
"""

import os
import time

import cv2
import numpy as np
import pyautogui


class ImageMatcher:
    """
    Screen-based template matcher using OpenCV's matchTemplate.

    Supports multiple matching methods, confidence thresholds,
    region-of-interest cropping, and grayscale matching for
    lighting-invariant detection.
    """

    # OpenCV matching methods — TM_CCOEFF_NORMED is generally best
    METHODS = {
        "ccoeff":   cv2.TM_CCOEFF_NORMED,
        "ccorr":    cv2.TM_CCORR_NORMED,
        "sqdiff":   cv2.TM_SQDIFF_NORMED,
    }

    def __init__(self, config: dict):
        self.config = config
        self.confidence = config.get("confidence_threshold", 0.8)
        self._template_cache: dict[str, np.ndarray] = {}

    # ---- internal helpers -------------------------------------------------

    def _grab_screen(self, region: tuple[int, int, int, int] | None = None) -> np.ndarray:
        """
        Capture the current screen (or a region) and return as a
        BGR numpy array (OpenCV format).

        Parameters
        ----------
        region : (x, y, w, h), optional
            Crop region. If None, captures the full screen.
        """
        screenshot = pyautogui.screenshot(region=region)
        frame = np.array(screenshot)
        # PIL gives RGB, OpenCV needs BGR
        return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

    def _load_template(self, template_path: str) -> np.ndarray:
        """Load and cache a template image."""
        if template_path not in self._template_cache:
            img = cv2.imread(template_path, cv2.IMREAD_COLOR)
            if img is None:
                raise FileNotFoundError(
                    f"Template image not found or unreadable: {template_path}"
                )
            self._template_cache[template_path] = img
        return self._template_cache[template_path]

    # ---- public API -------------------------------------------------------

    def find(
        self,
        template_path: str,
        confidence: float | None = None,
        region: tuple[int, int, int, int] | None = None,
        grayscale: bool = True,
        method: str = "ccoeff",
    ) -> tuple[int, int, float] | None:
        """
        Find a template image on screen.

        Parameters
        ----------
        template_path : str
            Path to the template PNG/JPG image.
        confidence : float
            Minimum confidence (0–1). Defaults to config threshold.
        region : (x, y, w, h), optional
            Limit search to a screen region for speed.
        grayscale : bool
            Convert both images to grayscale before matching
            (more robust to color shifts).
        method : str
            Matching method: "ccoeff" (default), "ccorr", or "sqdiff".

        Returns
        -------
        (center_x, center_y, confidence) if found, else None.
        """
        conf_threshold = confidence or self.confidence
        cv_method = self.METHODS.get(method, cv2.TM_CCOEFF_NORMED)

        screen = self._grab_screen(region)
        template = self._load_template(template_path)

        if grayscale:
            screen = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
            template = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)

        th, tw = template.shape[:2]

        # Ensure screen is larger than template
        sh, sw = screen.shape[:2]
        if th > sh or tw > sw:
            return None

        result = cv2.matchTemplate(screen, template, cv_method)

        if cv_method == cv2.TM_SQDIFF_NORMED:
            # For SQDIFF, lower = better
            min_val, _, min_loc, _ = cv2.minMaxLoc(result)
            match_conf = 1.0 - min_val
            match_loc = min_loc
        else:
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            match_conf = max_val
            match_loc = max_loc

        if match_conf < conf_threshold:
            return None

        # Calculate center of matched region
        cx = match_loc[0] + tw // 2
        cy = match_loc[1] + th // 2

        # Adjust for region offset
        if region:
            cx += region[0]
            cy += region[1]

        return (cx, cy, round(match_conf, 4))

    def find_all(
        self,
        template_path: str,
        confidence: float | None = None,
        region: tuple[int, int, int, int] | None = None,
        grayscale: bool = True,
        max_results: int = 20,
    ) -> list[tuple[int, int, float]]:
        """
        Find ALL occurrences of a template on screen.

        Returns a list of (center_x, center_y, confidence) tuples,
        sorted by confidence descending.
        """
        conf_threshold = confidence or self.confidence

        screen = self._grab_screen(region)
        template = self._load_template(template_path)

        if grayscale:
            screen_g = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
            template_g = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
        else:
            screen_g, template_g = screen, template

        th, tw = template_g.shape[:2]
        sh, sw = screen_g.shape[:2]
        if th > sh or tw > sw:
            return []

        result = cv2.matchTemplate(screen_g, template_g, cv2.TM_CCOEFF_NORMED)

        locations = np.where(result >= conf_threshold)
        matches = []

        for pt in zip(*locations[::-1]):  # (x, y) pairs
            cx = pt[0] + tw // 2
            cy = pt[1] + th // 2
            conf = float(result[pt[1], pt[0]])

            if region:
                cx += region[0]
                cy += region[1]

            # Non-maximum suppression: skip if too close to existing match
            too_close = False
            for mx, my, _ in matches:
                if abs(cx - mx) < tw * 0.5 and abs(cy - my) < th * 0.5:
                    too_close = True
                    break
            if not too_close:
                matches.append((cx, cy, round(conf, 4)))

            if len(matches) >= max_results:
                break

        matches.sort(key=lambda m: m[2], reverse=True)
        return matches

    def wait_for(
        self,
        template_path: str,
        timeout: float = 30.0,
        interval: float = 0.5,
        confidence: float | None = None,
        region: tuple[int, int, int, int] | None = None,
    ) -> tuple[int, int, float] | None:
        """
        Poll the screen until the template appears or timeout.

        Parameters
        ----------
        timeout : float
            Max seconds to wait.
        interval : float
            Seconds between screen captures.

        Returns
        -------
        (x, y, confidence) if found within timeout, else None.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = self.find(template_path, confidence=confidence, region=region)
            if result:
                return result
            time.sleep(interval)
        return None

    def wait_until_gone(
        self,
        template_path: str,
        timeout: float = 30.0,
        interval: float = 0.5,
        confidence: float | None = None,
        region: tuple[int, int, int, int] | None = None,
    ) -> bool:
        """
        Wait until a template is no longer visible on screen.
        Returns True if it disappeared within timeout, False otherwise.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = self.find(template_path, confidence=confidence, region=region)
            if result is None:
                return True
            time.sleep(interval)
        return False

    def save_screenshot(self, filepath: str,
                        region: tuple[int, int, int, int] | None = None):
        """
        Capture and save a screenshot — useful for creating templates.
        """
        screen = self._grab_screen(region)
        cv2.imwrite(filepath, screen)
        print(f"[image_matcher] 📸 Screenshot saved → {filepath}")
        return filepath

    def clear_cache(self):
        """Clear the template image cache."""
        self._template_cache.clear()
