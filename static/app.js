document.addEventListener('DOMContentLoaded', () => {
  // Password Visibility Toggle (Eye Icon)
  const togglePassBtns = document.querySelectorAll('.toggle-password');
  togglePassBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      const targetId = btn.dataset.target;
      const passInput = document.getElementById(targetId);
      if (passInput) {
        const isPassword = passInput.type === 'password';
        passInput.type = isPassword ? 'text' : 'password';
        btn.textContent = isPassword ? '🙈' : '👁️';
        btn.setAttribute('title', isPassword ? 'Hide Password' : 'Show Password');
      }
    });
  });

  // Client-side form validation for GitHub Repo URL
  const forms = document.querySelectorAll('form');
  forms.forEach(form => {
    form.addEventListener('submit', (e) => {
      const activeMode = form.querySelector('.mode-input')?.value;
      if (activeMode === 'github') {
        const repoUrl = document.getElementById('repo_url');
        if (repoUrl && (!repoUrl.value.trim() || !repoUrl.value.includes('github.com'))) {
          alert('Please enter a valid GitHub repository URL (e.g. https://github.com/user/repo).');
          e.preventDefault();
        }
      }
    });
  });
});
