import argparse
import os
import shutil
import sys
import time
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import (
	ElementClickInterceptedException,
	TimeoutException,
	WebDriverException,
	SessionNotCreatedException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.chrome.service import Service as ChromeService

try:
	# Optional dependency, used as fallback when local PATH chromedriver is incompatible
	from webdriver_manager.chrome import ChromeDriverManager
except Exception:  # pragma: no cover - remain optional until used
	ChromeDriverManager = None


def _hide_chromedriver_from_path():
	"""If an incompatible chromedriver is on PATH, hide it for this process.

	Selenium Manager works best when it can resolve the driver itself. If a
	chromedriver binary is present on PATH (e.g., via Homebrew) and is
	incompatible with the local Chrome version, Selenium may try to use it
	and fail. We remove that directory from PATH for this process.
	"""
	chromedriver_path = shutil.which("chromedriver")
	if not chromedriver_path:
		return
	driver_dir = os.path.dirname(os.path.realpath(chromedriver_path))
	path_entries = os.environ.get("PATH", "").split(os.pathsep)
	new_entries = [p for p in path_entries if os.path.realpath(p) != driver_dir]
	if len(new_entries) != len(path_entries):
		os.environ["PATH"] = os.pathsep.join(new_entries)


def get_chrome_driver(headless: bool = True) -> webdriver.Chrome:
	"""Create and return a Chrome WebDriver using Selenium Manager (no manual driver downloads).

	Args:
		headless: Run Chrome in headless mode (recommended for CI/servers).

	Returns:
		A configured Chrome WebDriver instance.
	"""
	options = webdriver.ChromeOptions()
	if headless:
		# new headless is more compatible with real browser behavior
		options.add_argument("--headless=new")
	# Conservative, cross-environment flags
	options.add_argument("--no-sandbox")
	options.add_argument("--disable-dev-shm-usage")
	options.add_argument("--disable-gpu")
	options.add_argument("--window-size=1366,900")
	# Reduce automation fingerprints
	options.add_experimental_option("excludeSwitches", ["enable-automation"])
	options.add_experimental_option("useAutomationExtension", False)
	options.add_argument("--disable-blink-features=AutomationControlled")
	options.add_argument("--lang=en-IN")
	options.add_argument(
		"--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
	)

	# Prefer webdriver-manager to avoid PATH chromedriver conflicts entirely
	if ChromeDriverManager is not None:
		driver_path = ChromeDriverManager().install()
		service = ChromeService(executable_path=driver_path)
		driver = webdriver.Chrome(service=service, options=options)
		# Patch navigator.webdriver and other properties early
		try:
			driver.execute_cdp_cmd(
				"Page.addScriptToEvaluateOnNewDocument",
				{"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"},
			)
		except Exception:
			pass
		return driver

	# Fallback: try Selenium Manager with PATH cleaned
	_hide_chromedriver_from_path()
	try:
		driver = webdriver.Chrome(options=options)
		try:
			driver.execute_cdp_cmd(
				"Page.addScriptToEvaluateOnNewDocument",
				{"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"},
			)
		except Exception:
			pass
		return driver
	except SessionNotCreatedException:
		# Re-raise if we couldn't use webdriver-manager above
		raise


def get_safari_driver() -> webdriver.Safari:
	"""Create and return a Safari WebDriver (macOS only).

	Note: You must enable 'Allow Remote Automation' in Safari > Preferences > Advanced.
	Headless is not supported for Safari.
	"""
	return webdriver.Safari()


def click_naukri_login(headless: bool = True, timeout: int = 20, email: str = "", password: str = "") -> None:
	"""Open naukri.com and click the Login button.

	This tries Chrome first; if unavailable, falls back to Safari on macOS.
	Saves a screenshot before and after clicking for quick verification.
	"""
	driver = None
	tried = []
	try:
		try:
			driver = get_chrome_driver(headless=headless)
			tried.append("chrome")
		except WebDriverException as e:
			# Fallback to Safari on macOS if Chrome isn't available
			tried.append(f"chrome: {e.__class__.__name__}")
			try:
				driver = get_safari_driver()
				tried.append("safari")
			except WebDriverException as e2:
				tried.append(f"safari: {e2.__class__.__name__}")
				raise

		wait = WebDriverWait(driver, timeout)
		start_url = (
			"https://login.naukri.com/nLogin/Login.php"
			if os.getenv("GITHUB_ACTIONS", "").lower() == "true"
			else "https://www.naukri.com/"
		)
		driver.get(start_url)


		# small settle
		time.sleep(1.0)
		
		# Attempt to dismiss common popups/cookie banners if they appear
		def try_click_css(selector: str):
			try:
				el = driver.find_element(By.CSS_SELECTOR, selector)
				driver.execute_script("arguments[0].click();", el)
				return True
			except Exception:
				return False

		def try_click_xpath(xpath: str):
			try:
				el = driver.find_element(By.XPATH, xpath)
				driver.execute_script("arguments[0].click();", el)
				return True
			except Exception:
				return False

		# give overlays a moment to render
		time.sleep(0.5)
		for _ in range(2):  # try twice in case of delayed render
			dismissed = False
			dismissed |= try_click_css("#onetrust-accept-btn-handler")  # OneTrust cookies
			dismissed |= try_click_css("button#onetrust-accept-btn-handler")
			dismissed |= try_click_css("#wzrk-cancel")  # CleverTap push prompt cancel
			dismissed |= try_click_css("#wzrk-confirm")
			dismissed |= try_click_xpath("//button[contains(., 'Accept')]")
			dismissed |= try_click_xpath("//button[contains(., 'Got it') or contains(., 'GOT IT')]")
			if not dismissed:
				break
			time.sleep(0.4)

		Path("screenshots").mkdir(exist_ok=True)
		driver.save_screenshot("screenshots/01_home.png")

		# If we're not already on the login page, click the Login link
		if "login" not in driver.current_url.lower():
			# Wait for the login link to be present (not necessarily clickable due to overlays)
			login_locators = [
				(By.ID, "login_Layer"),
				(By.CSS_SELECTOR, "a#login_Layer"),
				(By.CSS_SELECTOR, "a[title='Jobseeker Login']"),
				(By.XPATH, "//a[@id='login_Layer' or @title='Jobseeker Login' or contains(@class,'nI-gNb-lg-rg__login')]")
			]
			el = None
			last_exc = None
			for loc in login_locators:
				try:
					el = WebDriverWait(driver, max(6, timeout // 2)).until(EC.presence_of_element_located(loc))
					if el:
						break
				except TimeoutException as te:
					last_exc = te
					continue
			if not el:
				raise last_exc or TimeoutException("Login element not found")

			# Scroll and JS-click to avoid intermittent intercepts
			driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
			try:
				el.click()
			except Exception:
				driver.execute_script("arguments[0].click();", el)

			# Optional: wait briefly for resulting layer/navigation
			time.sleep(1.5)

			# Heuristic: either navigates to a login page or opens a login layer
			current_url = driver.current_url
			print(f"After click, URL: {current_url}")

			# Save a proof screenshot
			driver.save_screenshot("screenshots/02_after_click.png")

		# If credentials provided, try to fill them now
		if email and password:
			try:
				fill_credentials(driver, email=email, password=password, timeout=timeout)
				print("Filled email and password fields.")
				# Click the Login submit button
				try:
					click_login_submit(driver, timeout=timeout)
					print("Clicked the Login submit button.")
					# Proceed to profile flow
					try:
						navigate_profile_and_save(driver, timeout=timeout)
						print("Navigated to View profile, clicked edit, and pressed Save.")
					except TimeoutException:
						print("Profile/edit/save elements not found within timeout.")
				except TimeoutException:
					print("Login submit button not found within timeout.")
			except TimeoutException:
				print("Could not locate login input fields to fill within timeout.")

		# Soft assertion: URL contains 'login' or a visible username/email field appears
		try:
			wait.until(
				EC.any_of(
					EC.url_contains("login"),
					EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='email'], input[name*='user'], input[name*='email']")),
				)
			)
			print("Login action appears successful (login page or layer detected).")
		except TimeoutException:
			print("Clicked Login, but couldn't confirm login page/layer within timeout. Check screenshots.")

	finally:
		if driver is not None:
			driver.quit()
		if tried:
			print(f"Tried drivers: {', '.join(tried)}")


def _switch_to_frame_with_inputs(driver, email_locators, password_locators, timeout=5):
	# Try default content first
	try:
		for loc in email_locators + password_locators:
			if driver.find_elements(*loc):
				return True  # already in the right context
	except Exception:
		pass

	# Try each iframe
	iframes = driver.find_elements(By.TAG_NAME, "iframe")
	for idx, frame in enumerate(iframes):
		try:
			driver.switch_to.frame(frame)
			for loc in email_locators + password_locators:
				if driver.find_elements(*loc):
					return True
			driver.switch_to.default_content()
		except Exception:
			try:
				driver.switch_to.default_content()
			except Exception:
				pass
			continue
	return False


def fill_credentials(driver, email: str, password: str, timeout: int = 20) -> None:
	wait_short = WebDriverWait(driver, max(5, timeout // 2))

	email_locators = [
		(By.CSS_SELECTOR, "input[type='email']"),
		(By.CSS_SELECTOR, "input[id*='email' i]"),
		(By.CSS_SELECTOR, "input[name*='email' i]"),
		(By.CSS_SELECTOR, "input[placeholder*='Email' i]"),
		(By.CSS_SELECTOR, "input[placeholder*='Username' i]"),
		(By.ID, "usernameField"),
	]
	password_locators = [
		(By.CSS_SELECTOR, "input[type='password']"),
		(By.CSS_SELECTOR, "input[id*='pass' i]"),
		(By.CSS_SELECTOR, "input[name*='pass' i]"),
		(By.CSS_SELECTOR, "input[placeholder*='password' i]"),
		(By.ID, "passwordField"),
	]

	# Try to ensure correct context
	_switch_to_frame_with_inputs(driver, email_locators, password_locators, timeout=max(5, timeout // 2))

	# Find elements
	email_el = None
	for loc in email_locators:
		try:
			email_el = wait_short.until(EC.presence_of_element_located(loc))
			if email_el:
				break
		except TimeoutException:
			continue
	if not email_el:
		raise TimeoutException("Email/username field not found")

	pwd_el = None
	for loc in password_locators:
		try:
			pwd_el = wait_short.until(EC.presence_of_element_located(loc))
			if pwd_el:
				break
		except TimeoutException:
			continue
	if not pwd_el:
		raise TimeoutException("Password field not found")

	# Fill values
	driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", email_el)
	try:
		email_el.clear()
	except Exception:
		pass
	email_el.send_keys(email)

	driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", pwd_el)
	try:
		pwd_el.clear()
	except Exception:
		pass
	pwd_el.send_keys(password)

	# Proof screenshot
	Path("screenshots").mkdir(exist_ok=True)
	driver.save_screenshot("screenshots/03_filled_fields.png")


def click_login_submit(driver, timeout: int = 20) -> None:
	wait_short = WebDriverWait(driver, max(5, timeout // 2))

	submit_locators = [
		(By.CSS_SELECTOR, "button.btn-primary.loginButton"),
		(By.CSS_SELECTOR, "button.loginButton"),
		(By.XPATH, "//button[@type='submit' and contains(@class,'loginButton')]")
		,
		(By.XPATH, "//button[contains(., 'Login') or contains(., 'Log In')]")
		,
		(By.CSS_SELECTOR, "input[type='submit']"),
	]

	el = None
	last_exc = None
	for loc in submit_locators:
		try:
			el = wait_short.until(EC.presence_of_element_located(loc))
			if el:
				break
		except TimeoutException as te:
			last_exc = te
			continue
	if not el:
		raise last_exc or TimeoutException("Login submit button not found")

	driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
	try:
		el.click()
	except Exception:
		driver.execute_script("arguments[0].click();", el)

	time.sleep(1.5)
	Path("screenshots").mkdir(exist_ok=True)
	driver.save_screenshot("screenshots/04_after_submit.png")


def _switch_to_last_window_if_new(driver, before_handles):
	# If a new window/tab opened, switch to it
	after = driver.window_handles
	if len(after) > len(before_handles):
		new_handle = [h for h in after if h not in before_handles][-1]
		driver.switch_to.window(new_handle)
		return True
	return False


def navigate_profile_and_save(driver, timeout: int = 20) -> None:
	wait = WebDriverWait(driver, timeout)

	# Try clicking 'View profile'
	profile_locators = [
		(By.XPATH, "//a[normalize-space()='View profile']"),
		(By.CSS_SELECTOR, "a[href='/mnjuser/profile']"),
		(By.XPATH, "//a[contains(@href, '/mnjuser/profile')]")
	]
	before = driver.window_handles
	clicked = False
	for loc in profile_locators:
		try:
			el = wait.until(EC.presence_of_element_located(loc))
			driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
			try:
				el.click()
			except Exception:
				driver.execute_script("arguments[0].click();", el)
			clicked = True
			break
		except TimeoutException:
			continue

	if clicked:
		_switch_to_last_window_if_new(driver, before)
		# small wait for navigation
		time.sleep(1.0)
	else:
		# Fallback: navigate directly
		driver.get("https://www.naukri.com/mnjuser/profile")
		time.sleep(1.0)

	Path("screenshots").mkdir(exist_ok=True)
	driver.save_screenshot("screenshots/05_profile_page.png")

	# Click the edit icon (editOneTheme)
	edit_locators = [
		(By.XPATH, "//em[contains(@class,'icon') and contains(@class,'edit') and contains(normalize-space(.), 'editOneTheme')]") ,
		(By.CSS_SELECTOR, "em.icon.edit"),
	]
	el_edit = None
	for loc in edit_locators:
		try:
			el_edit = wait.until(EC.presence_of_element_located(loc))
			if el_edit:
				break
		except TimeoutException:
			continue
	if not el_edit:
		raise TimeoutException("Edit icon not found")

	driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el_edit)
	try:
		el_edit.click()
	except Exception:
		driver.execute_script("arguments[0].click();", el_edit)

	time.sleep(0.8)
	driver.save_screenshot("screenshots/06_edit_clicked.png")

	# Click Save button
	save_locators = [
		(By.ID, "saveBasicDetailsBtn"),
		(By.CSS_SELECTOR, "#saveBasicDetailsBtn"),
		(By.CSS_SELECTOR, "button#saveBasicDetailsBtn.btn-dark-ot"),
		(By.XPATH, "//button[@id='saveBasicDetailsBtn' or (contains(@class,'btn-dark-ot') and (normalize-space(.)='Save' or contains(@aria-label,'Save')))]"),
	]
	el_save = None
	for loc in save_locators:
		try:
			el_save = wait.until(EC.presence_of_element_located(loc))
			if el_save:
				break
		except TimeoutException:
			continue
	if not el_save:
		raise TimeoutException("Save button not found")

	driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el_save)
	try:
		el_save.click()
	except Exception:
		driver.execute_script("arguments[0].click();", el_save)

	time.sleep(1.0)
	driver.save_screenshot("screenshots/07_after_save.png")


def parse_args(argv=None):
	p = argparse.ArgumentParser(description="Automate naukri.com login button click with Selenium")
	p.add_argument("--headless", action="store_true", help="Run browser in headless mode (Chrome only)")
	p.add_argument("--timeout", type=int, default=20, help="Explicit wait timeout in seconds")
	p.add_argument("--email", default=None, help="Email/username (falls back to env NAUKRI_EMAIL if omitted)")
	p.add_argument("--password", dest="password", default=None, help="Password (falls back to env NAUKRI_PASSWORD if omitted)")
	return p.parse_args(argv)


def main(argv=None) -> int:
	args = parse_args(argv)
	def _resolve_credentials(arg_email, arg_password):
		email = arg_email or os.getenv("NAUKRI_EMAIL", "")
		password = arg_password or os.getenv("NAUKRI_PASSWORD", "")
		if not email or not password:
			print("Warning: missing credentials. Provide --email/--password or set NAUKRI_EMAIL/NAUKRI_PASSWORD.")
		return email, password

	# In GitHub Actions, always run headless and fail fast if secrets are missing
	if os.getenv("GITHUB_ACTIONS", "").lower() == "true":
		args.headless = True
		ci_email = os.getenv("NAUKRI_EMAIL", "")
		ci_password = os.getenv("NAUKRI_PASSWORD", "")
		if not ci_email or not ci_password:
			print("Error: NAUKRI_EMAIL/NAUKRI_PASSWORD not set in GitHub Actions secrets.")
			return 2

	res_email, res_password = _resolve_credentials(args.email, args.password)
	click_naukri_login(headless=args.headless, timeout=args.timeout, email=res_email, password=res_password)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())

