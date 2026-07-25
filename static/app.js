document.addEventListener('DOMContentLoaded', () => {
  // Tab switching logic for deployment options
  const tabBtns = document.querySelectorAll('.tab-btn');
  const tabContents = document.querySelectorAll('.tab-content');
  const modeInputs = document.querySelectorAll('.mode-input');

  tabBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      tabBtns.forEach(b => b.classList.remove('active'));
      tabContents.forEach(c => c.classList.remove('active'));
      
      const targetId = btn.dataset.target;
      modeInputs.forEach(input => {
        input.value = targetId === 'tab-zip' ? 'zip' : 'files';
      });

      btn.classList.add('active');
      const targetElement = document.getElementById(targetId);
      if (targetElement) {
        targetElement.classList.add('active');
      }
    });
  });

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

  // Client-side form validation before submitting
  const forms = document.querySelectorAll('form');
  forms.forEach(form => {
    form.addEventListener('submit', (e) => {
      const activeMode = form.querySelector('.mode-input')?.value;
      if (activeMode === 'files') {
        const botFile = document.getElementById('bot_file');
        if (botFile && !botFile.files.length) {
          alert('Please select a bot.py file to upload.');
          e.preventDefault();
        }
      } else if (activeMode === 'zip') {
        const zipFile = document.getElementById('zip_file');
        if (zipFile && !zipFile.files.length) {
          alert('Please select a project.zip file to upload.');
          e.preventDefault();
        }
      }
    });
  });
});
