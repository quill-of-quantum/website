(function () {
  "use strict";

  const elements = {
    authNotice: document.getElementById("authNotice"),
    controls: document.getElementById("rtcControls"),
    create: document.getElementById("createCall"),
    join: document.getElementById("joinCall"),
    copy: document.getElementById("copyInvite"),
    inviteWrap: document.getElementById("inviteWrap"),
    invite: document.getElementById("inviteUrl"),
    status: document.getElementById("rtcStatus"),
    localVideo: document.getElementById("localVideo"),
    remoteVideo: document.getElementById("remoteVideo"),
    mute: document.getElementById("toggleMute"),
    camera: document.getElementById("toggleCamera"),
    hangup: document.getElementById("hangup"),
    diagnostics: document.getElementById("rtcDiagnostics"),
    turnWarning: document.getElementById("turnWarning"),
  };
  let client = null;
  let sessionId = null;
  let localStream = null;
  let statsTimer = null;
  let statsRefreshing = false;
  let statsPeerId = null;

  async function api(path, options = {}) {
    const response = await fetch(path, {
      credentials: "same-origin",
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || !payload.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    return payload;
  }

  function setStatus(message, tone = "") {
    elements.status.textContent = message;
    elements.status.dataset.tone = tone;
  }

  async function acquireMedia() {
    if (localStream) return localStream;
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      throw new Error("摄像头和麦克风要求 HTTPS 安全页面");
    }
    localStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true },
      video: {
        width: { ideal: 640 },
        height: { ideal: 360 },
        frameRate: { ideal: 15, max: 15 },
      },
    });
    elements.localVideo.srcObject = localStream;
    return localStream;
  }

  async function startRtc(joinToken) {
    const media = await acquireMedia();
    const ice = await api(`/api/rtc/sessions/${encodeURIComponent(sessionId)}/ice-config`, { headers: {} });
    elements.turnWarning.hidden = ice.turn_available;
    client = new window.RtcClient({
      sessionId,
      joinToken,
      iceServers: ice.iceServers,
      localStream: media,
      joinTokenProvider: async () => {
        const result = await api(`/api/rtc/sessions/${encodeURIComponent(sessionId)}/join-token`, {
          method: "POST", body: "{}",
        });
        return result.join_token;
      },
      onRemoteStream(stream, peer) {
        elements.remoteVideo.srcObject = stream;
        setStatus(`已收到 ${peer.display_name || "对方"} 的媒体轨道，正在建立加密网络路径…`);
      },
      onPeerState(peerId, state) {
        if (state === "connected" || state === "data:open") {
          setStatus("WebRTC 已连接，正在确认网络路径…", "success");
          startStats(peerId);
        } else if (state === "ice:restarting") {
          setStatus("首次打洞未成功，正在自动重新协商一次…", "warning");
        } else if (state === "failed" || state === "ice:failed") {
          setStatus("打洞失败；当前没有 TURN 中继可以回退", "danger");
        } else if (state === "disconnected") {
          setStatus("对方网络已断开", "warning");
        }
      },
      onIceDiagnostics(peerId, info) {
        if (peerId === statsPeerId) return;
        const local = info.localCandidateTypes.join("/") || "无";
        const remote = info.remoteCandidateTypes.join("/") || "无";
        const phase = info.gatheringComplete ? "候选收集完成" : "正在收集候选";
        const retry = info.restartAttempts ? ` · 已重试 ${info.restartAttempts} 次` : "";
        elements.diagnostics.textContent = `${phase} · 本端 ${local} · 对端 ${remote}${retry}`;
      },
      onPeerLeft() {
        elements.remoteVideo.srcObject = null;
        statsPeerId = null;
        setStatus("对方已离开，可以等待其重新加入", "warning");
      },
      onError(error) {
        setStatus(`连接错误：${error.message}`, "danger");
      },
    });
    await client.connect();
    elements.hangup.disabled = false;
    elements.mute.disabled = false;
    elements.camera.disabled = false;
    setStatus("已进入会话，等待对方打开邀请链接…");
  }

  async function createCall() {
    elements.create.disabled = true;
    try {
      await acquireMedia();
      const created = await api("/api/rtc/sessions", {
        method: "POST",
        body: JSON.stringify({ kind: "call", role: "duplex", max_participants: 2 }),
      });
      sessionId = created.session.session_id;
      const invitation = await api(`/api/rtc/sessions/${sessionId}/invites`, {
        method: "POST", body: JSON.stringify({ role: "duplex" }),
      });
      elements.invite.value = new URL(invitation.invite_url, window.location.origin).href;
      elements.inviteWrap.hidden = false;
      await startRtc(created.join_token);
    } catch (error) {
      setStatus(error.message, "danger");
      elements.create.disabled = false;
    }
  }

  async function redeemInvite(inviteToken) {
    setStatus("正在验证一次性邀请…");
    try {
      await acquireMedia();
      const redeemed = await api("/api/rtc/invites/redeem", {
        method: "POST", body: JSON.stringify({ invite_token: inviteToken }),
      });
      history.replaceState(null, "", `${location.pathname}${location.search}`);
      sessionId = redeemed.session.session_id;
      await startRtc(redeemed.join_token);
    } catch (error) {
      setStatus(`无法加入：${error.message}`, "danger");
    }
  }

  function startStats(peerId) {
    if (statsPeerId === peerId && statsTimer) return;
    clearInterval(statsTimer);
    statsPeerId = peerId;
    const number = (value, digits = 0) => value == null || !Number.isFinite(value)
      ? "--" : value.toFixed(digits);
    const resolution = (sample) => sample?.width && sample?.height
      ? `${sample.width}×${sample.height}` : "--";
    const limitation = (reason) => ({
      none: "无", bandwidth: "网络带宽", cpu: "CPU/温度", other: "其他",
    })[reason] || "未知";
    const acceleration = (value) => value == null ? "未知" : (value ? "是" : "否");
    const refresh = async () => {
      if (statsRefreshing) return;
      statsRefreshing = true;
      try {
        const metrics = await client?.mediaStats(peerId);
        if (!metrics?.connection) return;
        const info = metrics.connection;
        const route = info.relayed ? "TURN 中继" : "P2P 打洞直连";
        const rtt = info.currentRoundTripTime == null ? "--" : `${Math.round(info.currentRoundTripTime * 1000)} ms`;
        const sent = metrics.outbound;
        const received = metrics.inbound;
        const profile = metrics.profile;
        const lines = [
          `${route} · ${info.protocol.toUpperCase()} · RTT ${rtt} · ${info.localCandidateType}/${info.remoteCandidateType}`,
          `链路估算 上行 ${number(info.availableOutgoingBitrate == null ? null : info.availableOutgoingBitrate / 1000)} kbps · 下行 ${number(info.availableIncomingBitrate == null ? null : info.availableIncomingBitrate / 1000)} kbps`,
          `发送 ${number(sent?.bitrateKbps)} kbps · 丢包 ${number(sent?.lossPercent, 1)}% · 抖动 ${number(sent?.jitterMs)} ms`,
          `发送画面 ${resolution(sent)} · ${number(sent?.framesPerSecond, 1)} fps · ${profile?.name || "--"}档 (${profile ? Math.round(profile.maxBitrate / 1000) : "--"} kbps上限)`,
          `编码 ${sent?.codec || "--"} · 限制 ${limitation(sent?.qualityLimitationReason)} · ${number(sent?.processingMsPerFrame, 1)} ms/帧 · 硬件加速 ${acceleration(sent?.powerEfficientEncoder)}`,
          `接收 ${number(received?.bitrateKbps)} kbps · 丢包 ${number(received?.lossPercent, 1)}% · 抖动 ${number(received?.jitterMs)} ms`,
          `接收画面 ${resolution(received)} · ${number(received?.framesPerSecond, 1)} fps · 累计丢帧 ${number(received?.framesDropped)}`,
          `解码 ${received?.codec || "--"} · ${number(received?.processingMsPerFrame, 1)} ms/帧 · 硬件加速 ${acceleration(received?.powerEfficientDecoder)}`,
        ];
        elements.diagnostics.textContent = lines.join("\n");
        const adaptation = await client.adaptVideo(peerId, metrics).catch(() => null);
        if (adaptation?.changed) {
          setStatus(`网络质量变化，视频已自动切换到${adaptation.profile.name}档`, "warning");
        } else {
          setStatus(info.relayed ? "已通过 TURN 建立加密连接" : "打洞成功：媒体正在端到端直连", "success");
        }
      } finally {
        statsRefreshing = false;
      }
    };
    refresh();
    statsTimer = setInterval(refresh, 2000);
  }

  async function init() {
    const auth = await fetch("/api/auth/status", { credentials: "same-origin" }).then((r) => r.json());
    if (!auth.logged_in) {
      elements.authNotice.hidden = false;
      elements.controls.hidden = true;
      setStatus("请先使用右上角登录，然后刷新本页", "warning");
      return;
    }
    elements.authNotice.hidden = true;
    elements.controls.hidden = false;
    const params = new URLSearchParams(location.hash.slice(1));
    const invite = params.get("invite");
    if (invite) {
      elements.create.hidden = true;
      elements.join.hidden = false;
      setStatus("邀请已验证到达；点击接受后才会申请摄像头和麦克风权限");
      elements.join.addEventListener("click", async () => {
        elements.join.disabled = true;
        await redeemInvite(invite);
      }, { once: true });
    }
  }

  elements.create.addEventListener("click", createCall);
  elements.copy.addEventListener("click", async () => {
    await navigator.clipboard.writeText(elements.invite.value);
    elements.copy.textContent = "已复制";
  });
  elements.mute.addEventListener("click", () => {
    const tracks = localStream?.getAudioTracks() || [];
    const enabled = tracks.some((track) => track.enabled);
    tracks.forEach((track) => { track.enabled = !enabled; });
    elements.mute.textContent = enabled ? "打开麦克风" : "静音";
  });
  elements.camera.addEventListener("click", () => {
    const tracks = localStream?.getVideoTracks() || [];
    const enabled = tracks.some((track) => track.enabled);
    tracks.forEach((track) => { track.enabled = !enabled; });
    elements.camera.textContent = enabled ? "打开摄像头" : "关闭摄像头";
  });
  elements.hangup.addEventListener("click", async () => {
    clearInterval(statsTimer);
    statsPeerId = null;
    client?.close();
    if (sessionId) {
      await api(`/api/rtc/sessions/${encodeURIComponent(sessionId)}`, {
        method: "DELETE", body: "{}",
      }).catch(() => {});
    }
    elements.localVideo.srcObject = null;
    elements.remoteVideo.srcObject = null;
    setStatus("通话已结束");
    elements.hangup.disabled = true;
  });
  window.addEventListener("beforeunload", () => client?.close());
  init().catch((error) => setStatus(error.message, "danger"));
})();
