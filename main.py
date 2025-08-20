import argparse
import os
import shutil
import sys
import time
import re
import imaplib
import email as py_email
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
from selenium.webdriver.common.keys import Keys

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


def click_naukri_login(
	headless: bool = True,
	timeout: int = 20,
	email: str = "",
	password: str = "",
	use_google: bool = False,
	google_email: str = "",
	google_password: str = "",
) -> None:
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

		# OTP login flow (default): click "Use OTP to Login", send OTP to email, fetch via IMAP, fill and verify
		try:
			start_otp_login(driver, email=email, timeout=timeout)
			print("Requested OTP to email.")
			Path("screenshots").mkdir(exist_ok=True)
			driver.save_screenshot("screenshots/03_otp_challenge.png")

			# Fetch OTP via IMAP
			imap_host = os.getenv("IMAP_HOST", "imap.gmail.com")
			imap_user = os.getenv("NAUKRI_EMAIL", email)
			imap_pass = os.getenv("NAUKRI_PASSWORD", password)
			otp_sender = os.getenv("OTP_SENDER", "naukri")
			otp_subject = os.getenv("OTP_SUBJECT", "otp|verification|login")
			otp = fetch_otp_via_imap(imap_host, imap_user, imap_pass, timeout=max(60, timeout), sender_hint=otp_sender, subject_hint=otp_subject)
			print(f"Fetched OTP: {'*' * len(otp)}")

			fill_otp(driver, otp, timeout=timeout)
			print("Entered OTP and submitted.")

			try:
				navigate_profile_and_save(driver, timeout=timeout)
				print("Navigated to View profile, clicked edit, and pressed Save.")
			except TimeoutException:
				print("Profile/edit/save elements not found within timeout.")
		except TimeoutException as te:
			print(f"OTP login flow failed within timeout: {te}")

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


def start_otp_login(driver, email: str, timeout: int = 20) -> None:
	wait = WebDriverWait(driver, timeout)
	# Click "Use OTP to Login" if present
	otp_link_locators = [
		(By.XPATH, "//a[contains(normalize-space(.), 'Use OTP') and contains(normalize-space(.), 'Login')]") ,
		(By.XPATH, "//button[contains(normalize-space(.), 'Use OTP') and contains(normalize-space(.), 'Login')]") ,
		(By.CSS_SELECTOR, "a[href*='otp' i], button[href*='otp' i]") ,
	]
	for loc in otp_link_locators:
		try:
			el = WebDriverWait(driver, max(4, timeout//2)).until(EC.element_to_be_clickable(loc))
			try:
				el.click()
			except Exception:
				driver.execute_script("arguments[0].click();", el)
			break
		except TimeoutException:
			continue

	# Enter email/username
	email_locators = [
		(By.ID, "usernameField"),
		(By.CSS_SELECTOR, "input[type='email']"),
		(By.CSS_SELECTOR, "input[name*='email' i]"),
		(By.CSS_SELECTOR, "input[placeholder*='Email' i]"),
	]
	email_el = None
	for loc in email_locators:
		try:
			email_el = wait.until(EC.visibility_of_element_located(loc))
			if email_el:
				break
		except TimeoutException:
			continue
	if not email_el:
		raise TimeoutException("Email field not found for OTP login")
	try:
		email_el.clear()
	except Exception:
		pass
	email_el.send_keys(email)

	# Send OTP button
	send_locators = [
		(By.XPATH, "//button[contains(., 'Send OTP') or contains(., 'Send One Time Password') or contains(., 'Login')]") ,
		(By.CSS_SELECTOR, "button[type='submit']"),
		(By.XPATH, "//input[@type='submit']"),
	]
	clicked = False
	for loc in send_locators:
		try:
			btn = wait.until(EC.element_to_be_clickable(loc))
			try:
				btn.click()
			except Exception:
				driver.execute_script("arguments[0].click();", btn)
			clicked = True
			break
		except TimeoutException:
			continue
	if not clicked:
		# fallback: press Enter in email field
		email_el.send_keys(Keys.ENTER)

	# Wait for OTP input UI to appear
	WebDriverWait(driver, max(10, timeout)).until(
		EC.any_of(
			EC.presence_of_all_elements_located((By.CSS_SELECTOR, "input[type='tel'][maxlength='1'], input[aria-label*='OTP' i]")),
			EC.presence_of_element_located((By.XPATH, "//input[contains(@name,'otp' i) or contains(@id,'otp' i)]")),
		)
	)
	time.sleep(0.5)


def fetch_otp_via_imap(host: str, user: str, password: str, timeout: int = 90, poll_interval: int = 5, sender_hint: str | None = None, subject_hint: str | None = None) -> str:
	"""Poll IMAP for the latest OTP email and extract a numeric code.

	Returns the first 6-8 digit code found, preferring 6.
	"""
	end_time = time.time() + max(15, timeout)
	last_error = None
	while time.time() < end_time:
		try:
			with imaplib.IMAP4_SSL(host) as M:
				M.login(user, password)
				M.select('INBOX')
				# Look for UNSEEN first; fallback to recent ALL
				typ, data = M.search(None, 'UNSEEN')
				ids = data[0].split() if typ == 'OK' else []
				if not ids:
					typ, data = M.search(None, 'ALL')
					ids = data[0].split()[-10:] if typ == 'OK' else []  # recent 10
				for msg_id in reversed(ids):  # newest first
					typ, msg_data = M.fetch(msg_id, '(RFC822)')
					if typ != 'OK' or not msg_data:
						continue
					msg = py_email.message_from_bytes(msg_data[0][1])
					from_addr = msg.get('From', '')
					subject = msg.get('Subject', '')
					if sender_hint and sender_hint.lower() not in from_addr.lower():
						# if hint provided, filter by it
						if not (subject_hint and any(h in subject.lower() for h in subject_hint.split('|'))):
							continue
					# extract text
					body_text = ""
					if msg.is_multipart():
						for part in msg.walk():
							ctype = part.get_content_type()
							if ctype in ('text/plain', 'text/html'):
								try:
									body_text += part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8', errors='ignore') + "\n"
								except Exception:
									continue
					else:
						try:
							body_text = msg.get_payload(decode=True).decode(msg.get_content_charset() or 'utf-8', errors='ignore')
						except Exception:
							body_text = msg.get_payload() or ''
					# Find OTP numbers
					codes = re.findall(r"\b(\d{4,8})\b", body_text)
					# Prefer 6-digit
					codes_sorted = sorted(codes, key=lambda c: (abs(len(c) - 6), -len(c)))
					if codes_sorted:
						return codes_sorted[0]
		except Exception as e:
			last_error = e
		time.sleep(poll_interval)
	raise TimeoutException(f"Could not retrieve OTP via IMAP within {timeout}s. Last error: {last_error}")


def fill_otp(driver, code: str, timeout: int = 20) -> None:
	wait = WebDriverWait(driver, timeout)
	digits = list(code.strip())
	# Try multi-input OTP fields first
	inputs = driver.find_elements(By.CSS_SELECTOR, "input[type='tel'][maxlength='1'], input[aria-label*='OTP' i]")
	if inputs and len(inputs) >= len(digits):
		for i, d in enumerate(digits):
			try:
				inputs[i].clear()
			except Exception:
				pass
			inputs[i].send_keys(d)
		Path("screenshots").mkdir(exist_ok=True)
		driver.save_screenshot("screenshots/04_otp_filled.png")
	else:
		# Single field fallback
		single_locators = [
			(By.XPATH, "//input[contains(@name,'otp' i) or contains(@id,'otp' i)]"),
			(By.CSS_SELECTOR, "input[name*='otp' i], input[id*='otp' i]")
		]
		field = None
		for loc in single_locators:
			try:
				field = wait.until(EC.visibility_of_element_located(loc))
				if field:
					break
			except TimeoutException:
				continue
		if not field:
			raise TimeoutException("OTP input field not found")
		try:
			field.clear()
		except Exception:
			pass
		field.send_keys(code)
		driver.save_screenshot("screenshots/04_otp_filled.png")

	# Click Verify/Submit
	verify_locators = [
		(By.XPATH, "//button[contains(., 'Verify') or contains(., 'Submit') or contains(., 'Login')]") ,
		(By.CSS_SELECTOR, "button[type='submit']"),
		(By.XPATH, "//input[@type='submit']"),
	]
	for loc in verify_locators:
		try:
			btn = WebDriverWait(driver, max(4, timeout//2)).until(EC.element_to_be_clickable(loc))
			try:
				btn.click()
			except Exception:
				driver.execute_script("arguments[0].click();", btn)
			break
		except TimeoutException:
			continue
	time.sleep(1.0)


def google_sign_in(driver, g_email: str, g_password: str, timeout: int = 30) -> None:
	wait = WebDriverWait(driver, timeout)
	# Ensure the login layer is visible; if not on login page, open it using existing flow above.
	# Click the "Sign in with Google" button
	google_btn_locators = [
		# Most reliable: find the visible control that contains the span text
		(By.XPATH, "//span[normalize-space()='Sign in with Google']/ancestor::*[self::button or self::a or self::div][1]"),
		# Generic: any clickable element with the text
		(By.XPATH, "//*[self::button or self::a or self::div][contains(normalize-space(.), 'Sign in with Google')]"),
		# Attribute hints
		(By.XPATH, "//button[contains(@aria-label,'Sign in with Google') or contains(@class,'google') or contains(@data-qa,'google')]"),
		(By.CSS_SELECTOR, "button[aria-label*='Sign in with Google' i]"),
		(By.CSS_SELECTOR, "div.social-media .google"),
		(By.CSS_SELECTOR, "div.google"),
	]
	btn = None
	for loc in google_btn_locators:
		try:
			btn = wait.until(EC.element_to_be_clickable(loc))
			if btn:
				break
		except TimeoutException:
			continue
	if not btn:
		raise TimeoutException("Google Sign-In button not found")

	before = driver.window_handles
	driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
	try:
		btn.click()
	except Exception:
		driver.execute_script("arguments[0].click();", btn)

	time.sleep(1.0)
	# Switch to Google Accounts window/tab if a new one opened
	after = driver.window_handles
	target_handle = None
	if len(after) > len(before):
		for h in after:
			if h not in before:
				driver.switch_to.window(h)
				target_handle = h
				break
	# If no new window, stay in current and continue

	# Now perform Google auth
	# Sometimes there's an account chooser; click "Use another account" if present
	try:
		use_another = WebDriverWait(driver, 5).until(
			EC.presence_of_element_located((By.XPATH, "//div[@role='button' and .//div[text()='Use another account']]"))
		)
		try:
			use_another.click()
		except Exception:
			driver.execute_script("arguments[0].click();", use_another)
	except TimeoutException:
		pass

	# Email step
	email_locators = [
		(By.ID, "identifierId"),
		(By.NAME, "identifier"),
		(By.CSS_SELECTOR, "input[type='email'][id='identifierId']"),
		(By.CSS_SELECTOR, "input[type='email'][name='identifier']"),
	]
	email_input = None
	last_exc = None
	for loc in email_locators:
		try:
			email_input = wait.until(EC.visibility_of_element_located(loc))
			if email_input:
				break
		except TimeoutException as te:
			last_exc = te
			continue
	if not email_input:
		raise last_exc or TimeoutException("Google email input not found")

	try:
		email_input.clear()
	except Exception:
		pass
	email_input.send_keys(g_email)
	Path("screenshots").mkdir(exist_ok=True)
	driver.save_screenshot("screenshots/03a_google_email_filled.png")

	email_next_locators = [
		(By.ID, "identifierNext"),
		(By.XPATH, "//span[normalize-space()='Next']/ancestor::*[self::button or self::div][@role='button'][1]"),
		(By.XPATH, "//*[@id='identifierNext' or @jsname='LgbsSe'][.//span[normalize-space()='Next']]")
	]
	clicked_next = False
	for loc in email_next_locators:
		try:
			next_btn = WebDriverWait(driver, 8).until(EC.element_to_be_clickable(loc))
			try:
				next_btn.click()
			except Exception:
				driver.execute_script("arguments[0].click();", next_btn)
			clicked_next = True
			break
		except TimeoutException:
			continue
	if not clicked_next:
		# Fallback: press Enter
		email_input.send_keys(Keys.ENTER)

	time.sleep(0.8)
	driver.save_screenshot("screenshots/03b_google_after_email_next.png")

	# Password step
	pwd_locators = [
		(By.NAME, "Passwd"),
		(By.CSS_SELECTOR, "input[type='password'][name='Passwd']"),
	]
	pwd_input = None
	last_exc = None
	for loc in pwd_locators:
		try:
			pwd_input = wait.until(EC.visibility_of_element_located(loc))
			if pwd_input:
				break
		except TimeoutException as te:
			last_exc = te
			continue
	if not pwd_input:
		raise last_exc or TimeoutException("Google password input not found")

	try:
		pwd_input.clear()
	except Exception:
		pass
	pwd_input.send_keys(g_password)
	driver.save_screenshot("screenshots/03c_google_password_filled.png")

	pwd_next_locators = [
		(By.ID, "passwordNext"),
		(By.XPATH, "//span[normalize-space()='Next']/ancestor::*[self::button or self::div][@role='button'][1]"),
	]
	clicked_pwd_next = False
	for loc in pwd_next_locators:
		try:
			pwd_next = WebDriverWait(driver, 8).until(EC.element_to_be_clickable(loc))
			try:
				pwd_next.click()
			except Exception:
				driver.execute_script("arguments[0].click();", pwd_next)
			clicked_pwd_next = True
			break
		except TimeoutException:
			continue
	if not clicked_pwd_next:
		pwd_input.send_keys(Keys.ENTER)

	# Wait for redirect back to Naukri
	WebDriverWait(driver, max(10, timeout)).until(
		EC.any_of(
			EC.url_contains("naukri.com"),
			EC.url_matches(r"https?://.*naukri\.com/.*"),
		)
	)
	# If a new window was used and we're still on Google, try switching back to any Naukri window
	for h in driver.window_handles:
		try:
			driver.switch_to.window(h)
			if "naukri.com" in (driver.current_url or "").lower():
				break
		except Exception:
			continue
	Path("screenshots").mkdir(exist_ok=True)
	driver.save_screenshot("screenshots/03_google_after_login.png")


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
	p = argparse.ArgumentParser(description="Automate naukri.com login via OTP (IMAP) and profile update with Selenium")
	p.add_argument("--headless", action="store_true", help="Run browser in headless mode (Chrome only)")
	p.add_argument("--timeout", type=int, default=20, help="Explicit wait timeout in seconds")
	return p.parse_args(argv)


def main(argv=None) -> int:
	args = parse_args(argv)

	# OTP + IMAP flow: reuse NAUKRI_EMAIL as login email and IMAP username; NAUKRI_PASSWORD as IMAP app password
	login_email = os.getenv("NAUKRI_EMAIL", "")
	imap_pass = os.getenv("NAUKRI_PASSWORD", "")
	if not login_email or not imap_pass:
		print("Error: NAUKRI_EMAIL/NAUKRI_PASSWORD must be set (email + IMAP app password).")
		return 2

	# In GitHub Actions, always run headless
	if os.getenv("GITHUB_ACTIONS", "").lower() == "true":
		args.headless = True

	click_naukri_login(
		headless=args.headless,
		timeout=args.timeout,
		email=login_email,
		password=imap_pass,
		use_google=False,
		google_email="",
		google_password="",
	)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())

