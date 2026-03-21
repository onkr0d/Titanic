// Enable Firebase App Check debug token in development
if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
  self.FIREBASE_APPCHECK_DEBUG_TOKEN = true;
}

// Check for dark mode preference before the page loads
if (window.matchMedia('(prefers-color-scheme: dark)').matches) {
  document.documentElement.classList.add('dark');
  document.documentElement.style.backgroundColor = '#111827'; // dark:bg-gray-900
} else {
  document.documentElement.style.backgroundColor = '#ffffff'; // bg-white
}
