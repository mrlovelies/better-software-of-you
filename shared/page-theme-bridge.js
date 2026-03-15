/* Theme bridge: syncs dark mode from parent React hub to iframe pages */
(function() {
  // Read theme from query param on initial load
  var params = new URLSearchParams(window.location.search);
  if (params.get('theme') === 'dark') {
    document.documentElement.classList.add('dark');
  }

  // Listen for theme changes from parent hub
  window.addEventListener('message', function(e) {
    if (e.data && e.data.type === 'soy-theme') {
      if (e.data.theme === 'dark') {
        document.documentElement.classList.add('dark');
      } else {
        document.documentElement.classList.remove('dark');
      }
    }
  });
})();
