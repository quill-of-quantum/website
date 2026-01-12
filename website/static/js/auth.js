class AuthManager {
  constructor() {
    this.currentUser = null;
    this.isLoggedIn = false;
    this.callbacks = [];
    this.statusWidget = null;
  }

  // 添加登录状态变化监听器
  onAuthChange(callback) {
    this.callbacks.push(callback);
  }

  // 通知所有监听器
  notifyAuthChange() {
    this.callbacks.forEach(cb => cb(this.isLoggedIn, this.currentUser));
  }

  // 检查登录状态
  async checkLoginStatus() {
    try {
      const res = await fetch('/api/auth/status');
      const data = await res.json();
      this.isLoggedIn = data.logged_in;
      this.currentUser = data.user;
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
          z-index: 1000;
          left: 0;
          top: 0;
          width: 100%;
          height: 100%;
          background-color: rgba(0,0,0,0.4);
        }
        .auth-modal-content {
          background-color: #fefefe;
          margin: 15% auto;
          padding: 20px;
          border-radius: 12px;
          width: 300px;
          text-align: center;
        }
        .auth-modal input {
          width: 100%;
          padding: 10px;
          margin: 8px 0;
          border: 1px solid #ddd;
          border-radius: 6px;
          box-sizing: border-box;
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
          <h3>管理员登录</h3>
          <input type="text" id="loginUsername" placeholder="用户名">
          <input type="password" id="loginPassword" placeholder="密码">
          <div style="margin-top:16px;">
            <button onclick="authManager.doLogin()">登录</button>
            <button onclick="authManager.closeLoginModal()">取消</button>
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
        this.isLoggedIn = true;
        this.currentUser = data.user;
        this.closeLoginModal();
        this.updateLoginUI();
        this.notifyAuthChange();
        this.showMessage('✅ 登录成功');
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
      this.isLoggedIn = false;
      this.currentUser = null;
      this.updateLoginUI();
      this.notifyAuthChange();
      this.showMessage('✅ 已退出登录');
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

  // 创建登录状态显示组件
  createLoginStatusWidget() {
    // 移除可能存在的旧组件
    const existing = document.getElementById('loginStatus');
    if (existing) {
      existing.remove();
    }

    this.statusWidget = document.createElement('div');
    this.statusWidget.id = 'loginStatus';
    this.statusWidget.className = 'login-status';
    this.statusWidget.innerHTML = `
      <span id="loginText">检查登录状态...</span>
      <button id="loginBtn" class="btn btn-sm" style="margin-left:8px;">登录</button>
    `;
    document.body.appendChild(this.statusWidget);

    // 绑定事件
    this.updateLoginUI();
    
    return this.statusWidget;
  }

  // 更新登录UI
  updateLoginUI() {
    const loginText = document.getElementById('loginText');
    const loginBtn = document.getElementById('loginBtn');
    
    if (loginText && loginBtn) {
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
