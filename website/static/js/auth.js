class AuthManager {
  constructor() {
    this.currentUser = null;
    this.isLoggedIn = false;
    this.role = null;
    this.permissions = new Set();
    this.authStatus = null;
    this.callbacks = [];
    this.statusWidget = null;
  }

  // 添加登录状态变化监听器
  onAuthChange(callback) {
    this.callbacks.push(callback);
  }

  // 通知所有监听器
  notifyAuthChange() {
    this.callbacks.forEach(cb => cb(this.isLoggedIn, this.currentUser, this.authStatus));
  }

  applyAuthStatus(data) {
    this.authStatus = data || null;
    this.isLoggedIn = Boolean(data && data.logged_in);
    this.currentUser = this.isLoggedIn ? data.user : null;
    this.role = this.isLoggedIn ? data.role : null;
    this.permissions = new Set(
      this.isLoggedIn && Array.isArray(data.permissions) ? data.permissions : []
    );
  }

  hasPermission(permission) {
    return this.permissions.has(permission);
  }

  updatePermissionUI() {
    document.querySelectorAll('[data-permission]').forEach(element => {
      element.hidden = !this.hasPermission(element.dataset.permission);
    });
  }

  // 检查登录状态
  async checkLoginStatus() {
    try {
      const res = await fetch('/api/auth/status');
      const data = await res.json();
      this.applyAuthStatus(data);
      this.updateLoginUI();
      this.notifyAuthChange();
      return data;
    } catch (e) {
      console.error('检查登录状态失败:', e);
      return null;
    }
  }

  // 显示登录模态框
  showLoginModal() {
    let modal = document.getElementById('loginModal');
    if (!modal) {
      this.createLoginModal();
      modal = document.getElementById('loginModal');
    }
    modal.style.display = 'block';
    const usernameInput = document.getElementById('loginUsername');
    if (usernameInput) usernameInput.focus();
  }

  // 创建登录模态框
  createLoginModal() {
    // 添加样式
    if (!document.getElementById('authStyles')) {
      const style = document.createElement('style');
      style.id = 'authStyles';
      style.textContent = `
        .auth-modal {
          display: none;
          position: fixed;
          z-index: 4000;
          left: 0;
          top: 0;
          width: 100%;
          height: 100%;
          background-color: rgba(0,0,0,0.4);
        }
        .auth-modal-content {
          background-color: #fefefe;
          margin: min(15vh, 120px) auto;
          padding: 26px;
          border: 1px solid #e5e7eb;
          border-radius: 16px;
          width: min(340px, calc(100% - 32px));
          text-align: center;
          box-shadow: 0 24px 60px rgba(15,23,42,.18);
        }
        .auth-modal input {
          width: 100%;
          padding: 10px;
          margin: 8px 0;
          border: 1px solid #ddd;
          border-radius: 6px;
          box-sizing: border-box;
        }
        .auth-modal-actions {
          display: flex;
          gap: 10px;
          justify-content: center;
          margin-top: 16px;
        }
        .auth-modal-links {
          display: flex;
          justify-content: center;
          gap: 16px;
          margin-top: 18px;
          padding-top: 16px;
          border-top: 1px solid #e5e7eb;
          font-size: 14px;
        }
        .login-status {
          background: rgba(255,255,255,0.9);
          padding: 8px 12px;
          border-radius: 999px;
          font-size: 0.9rem;
          box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }
      `;
      document.head.appendChild(style);
    }
    
    const modalHtml = `
      <div id="loginModal" class="auth-modal">
        <div class="auth-modal-content">
          <h3>账户登录</h3>
          <form onsubmit="event.preventDefault(); authManager.doLogin();">
            <input type="text" id="loginUsername" placeholder="用户名" autocomplete="username">
            <input type="password" id="loginPassword" placeholder="密码" autocomplete="current-password">
            <div class="auth-modal-actions">
              <button class="btn btn-primary" type="submit">登录</button>
              <button class="btn" type="button" onclick="authManager.closeLoginModal()">取消</button>
            </div>
          </form>
          <div class="auth-modal-links">
            <a href="/register">注册账户</a>
            <a href="/forgot-password">忘记用户/密码</a>
          </div>
        </div>
      </div>
    `;
    
    document.body.insertAdjacentHTML('beforeend', modalHtml);
  }

  // 关闭登录模态框
  closeLoginModal() {
    const modal = document.getElementById('loginModal');
    if (modal) {
      modal.style.display = 'none';
      document.getElementById('loginUsername').value = '';
      document.getElementById('loginPassword').value = '';
    }
  }

  // 执行登录
  async doLogin() {
    const username = document.getElementById('loginUsername').value;
    const password = document.getElementById('loginPassword').value;
    
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password })
      });
      
      const data = await res.json();
      if (data.status === 'success') {
        this.applyAuthStatus(data);
        this.closeLoginModal();
        this.updateLoginUI();
        this.notifyAuthChange();
        window.location.reload();
      } else {
        alert('登录失败: ' + data.message);
      }
    } catch (e) {
      alert('登录出错: ' + e.message);
    }
  }

  // 退出登录
  async doLogout() {
    try {
      await fetch('/api/auth/logout', { method: 'POST' });
      this.applyAuthStatus(null);
      this.updateLoginUI();
      this.notifyAuthChange();
      window.location.reload();
    } catch (e) {
      alert('退出失败: ' + e.message);
    }
  }

  // 需要登录的操作包装器
  requireLogin() {
    if (!this.isLoggedIn) {
      if (confirm('此操作需要登录，是否现在登录？')) {
        this.showLoginModal();
      }
      return false;
    }
    return true;
  }

  requirePermission(permission, message='当前账户没有执行此操作的权限') {
    if (!this.requireLogin()) return false;
    if (!this.hasPermission(permission)) {
      alert(message);
      return false;
    }
    return true;
  }

  // 创建登录状态显示组件
  createLoginStatusWidget() {
    const mount = document.querySelector('[data-site-auth-slot]');
    const existing = document.getElementById('loginStatus');
    if (existing) {
      this.statusWidget = existing;
      if (mount && existing.parentElement !== mount) {
        mount.appendChild(existing);
      }
      existing.style.position = 'static';
      this.updateLoginUI();
      return existing;
    }

    this.statusWidget = document.createElement('div');
    this.statusWidget.id = 'loginStatus';
    this.statusWidget.className = 'login-status';
    this.statusWidget.innerHTML = `
      <span id="loginText">检查登录状态...</span>
      <a id="adminLink" class="btn btn-sm" href="/1/" hidden style="display:none;">管理</a>
      <button id="loginBtn" class="btn btn-sm" style="margin-left:8px;">登录</button>
    `;
    (mount || document.body).appendChild(this.statusWidget);
    if (mount) {
      this.statusWidget.style.position = 'static';
    }

    // 绑定事件
    this.updateLoginUI();
    
    return this.statusWidget;
  }

  // 更新登录UI
  updateLoginUI() {
    const loginText = document.getElementById('loginText');
    const loginBtn = document.getElementById('loginBtn');
    const adminLink = document.getElementById('adminLink');
    
    if (loginText && loginBtn) {
      if (this.statusWidget) {
        this.statusWidget.dataset.loggedIn = String(this.isLoggedIn);
        this.statusWidget.dataset.role = this.role || '';
      }
      if (this.isLoggedIn) {
        loginText.textContent = `👤 ${this.currentUser}`;
        loginBtn.textContent = '退出';
        loginBtn.onclick = () => this.doLogout();
      } else {
        loginText.textContent = '未登录';
        loginBtn.textContent = '登录';
        loginBtn.onclick = () => this.showLoginModal();
      }
    }
    if (adminLink) {
      adminLink.href = this.role === 'admin' ? '/1/' : '/account';
      adminLink.hidden = !this.isLoggedIn;
      adminLink.style.display = this.isLoggedIn ? 'inline-flex' : 'none';
    }
    this.updatePermissionUI();
  }

  // 显示消息
  showMessage(message) {
    // 查找页面中的状态显示元素
    const statusElements = [
      document.getElementById('uploadStatus'),
      document.getElementById('statusMessage')
    ].filter(el => el);

    if (statusElements.length > 0) {
      statusElements[0].textContent = message;
    } else {
      console.log(message);
    }
  }
}

// 创建全局实例
window.authManager = new AuthManager();
