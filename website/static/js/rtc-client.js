(function () {
  "use strict";

  class RtcClient {
    constructor(options) {
      this.sessionId = options.sessionId;
      this.initialJoinToken = options.joinToken || null;
      this.joinTokenProvider = options.joinTokenProvider;
      this.iceServers = options.iceServers || [];
      this.localStream = options.localStream || null;
      this.onRemoteStream = options.onRemoteStream || (() => {});
      this.onPeerState = options.onPeerState || (() => {});
      this.onPeerLeft = options.onPeerLeft || (() => {});
      this.onIceDiagnostics = options.onIceDiagnostics || (() => {});
      this.onError = options.onError || (() => {});
      this.socket = null;
      this.participant = null;
      this.peers = new Map();
      this.pendingSignals = [];
      this.closed = false;
      this.videoProfiles = options.videoProfiles || [
        { name: "省流", width: 320, height: 180, frameRate: 10, maxBitrate: 250000 },
        { name: "标准", width: 640, height: 360, frameRate: 15, maxBitrate: 700000 },
        { name: "清晰", width: 960, height: 540, frameRate: 20, maxBitrate: 1300000 },
      ];
      this.statsHistory = new Map();
      this.captureProfileIndex = null;
    }

    async connect() {
      if (!window.io) throw new Error("Socket.IO 客户端未加载");
      if (this.socket) throw new Error("RTC 客户端已经启动");
      return new Promise((resolve, reject) => {
        let settled = false;
        this.socket = window.io("/rtc", {
          transports: ["websocket", "polling"],
          reconnection: false,
          forceNew: true,
        });

        this.socket.on("connect", async () => {
          try {
            const token = this.initialJoinToken || await this.joinTokenProvider();
            this.initialJoinToken = null;
            this.socket.emit("rtc_join", { join_token: token }, async (response) => {
              if (!response || !response.ok) {
                const error = new Error(response?.error || "加入 RTC 会话失败");
                if (!settled) reject(error);
                this.onError(error);
                return;
              }
              this.participant = response.participant;
              try {
                for (const peer of response.peers || []) await this._ensurePeer(peer);
                const queuedSignals = this.pendingSignals.splice(0);
                for (const message of queuedSignals) await this._handleSignal(message);
                settled = true;
                resolve(response);
              } catch (error) {
                if (!settled) reject(error);
                this.onError(error);
              }
            });
          } catch (error) {
            if (!settled) reject(error);
            this.onError(error);
          }
        });

        this.socket.on("connect_error", (error) => {
          if (!settled) reject(error);
          this.onError(error);
        });
        this.socket.on("rtc_peer_joined", (peer) => {
          if (peer?.participant_id !== this.participant?.participant_id) {
            this._ensurePeer(peer).catch(this.onError);
          }
        });
        this.socket.on("rtc_peer_left", (event) => {
          this._removePeer(event?.participant_id);
          this.onPeerLeft(event?.participant_id);
        });
        this.socket.on("rtc_signal", (message) => {
          if (!this.participant) {
            this.pendingSignals.push(message);
            return;
          }
          this._handleSignal(message).catch(this.onError);
        });
        this.socket.on("rtc_session_ended", () => this.close(false));
      });
    }

    async setLocalStream(stream) {
      this.localStream = stream;
      for (const state of this.peers.values()) {
        const existing = new Set(state.pc.getSenders().map((sender) => sender.track).filter(Boolean));
        for (const track of stream?.getTracks() || []) {
          if (!existing.has(track)) {
            state.pc.addTrack(track, stream);
          }
        }
      }
    }

    async _ensurePeer(peer) {
      const peerId = peer?.participant_id;
      if (!peerId || this.peers.has(peerId)) return this.peers.get(peerId);
      const pc = new RTCPeerConnection({ iceServers: this.iceServers });
      const state = {
        pc,
        peer,
        initiator: this.participant.participant_id.localeCompare(peerId) < 0,
        polite: this.participant.participant_id.localeCompare(peerId) > 0,
        makingOffer: false,
        ignoreOffer: false,
        isSettingRemoteAnswerPending: false,
        localCandidateTypes: new Set(),
        remoteCandidateTypes: new Set(),
        iceGatheringComplete: false,
        iceRestartAttempts: 0,
        iceFailureHandled: false,
        pendingCandidates: [],
        profileIndex: Math.min(1, this.videoProfiles.length - 1),
        badSamples: 0,
        goodSamples: 0,
        lastProfileChange: 0,
      };
      this.peers.set(peerId, state);

      for (const track of this.localStream?.getTracks() || []) {
        pc.addTrack(track, this.localStream);
      }
      if (state.initiator) {
        const channel = pc.createDataChannel("rtc-control", { ordered: true });
        this._configureDataChannel(channel, peerId);
      }
      pc.ondatachannel = (event) => this._configureDataChannel(event.channel, peerId);
      pc.ontrack = (event) => {
        const stream = event.streams?.[0] || new MediaStream([event.track]);
        this.onRemoteStream(stream, peer);
      };
      pc.onicecandidate = (event) => {
        if (event.candidate) {
          const type = this._candidateType(event.candidate);
          if (type) state.localCandidateTypes.add(type);
        } else {
          state.iceGatheringComplete = true;
        }
        this._emitIceDiagnostics(peerId, state);
        this._sendSignal(peerId, "candidate", event.candidate ? event.candidate.toJSON() : null);
      };
      pc.onconnectionstatechange = () => {
        // ICE owns failure/restart handling below. Other states are still
        // useful to the UI and applications embedding this client.
        if (pc.connectionState !== "failed") {
          this.onPeerState(peerId, pc.connectionState, peer);
        }
        if (pc.connectionState === "connected") {
          this._applyPeerProfile(state).catch(() => {});
          this._syncCaptureProfile().catch(() => {});
        }
      };
      pc.oniceconnectionstatechange = () => {
        if (pc.iceConnectionState === "failed") {
          if (state.iceFailureHandled) return;
          state.iceFailureHandled = true;
          if (state.iceRestartAttempts < 1) {
            state.iceRestartAttempts += 1;
            this.onPeerState(peerId, "ice:restarting", peer);
            pc.restartIce();
            return;
          }
          this.onPeerState(peerId, "ice:failed", peer);
          return;
        }
        state.iceFailureHandled = false;
        this.onPeerState(peerId, `ice:${pc.iceConnectionState}`, peer);
      };
      pc.onnegotiationneeded = async () => {
        // A deterministic offerer avoids simultaneous offers causing an ICE
        // generation to be rolled back (notably unreliable on mobile Safari).
        if (!state.initiator) return;
        try {
          state.makingOffer = true;
          await pc.setLocalDescription();
          await this._sendSignal(peerId, "description", pc.localDescription.toJSON());
        } catch (error) {
          this.onError(error);
        } finally {
          state.makingOffer = false;
        }
      };
      return state;
    }

    _configureDataChannel(channel, peerId) {
      channel.onopen = () => {
        channel.send(JSON.stringify({ type: "ready", at: Date.now() }));
        this.onPeerState(peerId, "data:open", this.peers.get(peerId)?.peer);
      };
      channel.onclose = () => this.onPeerState(peerId, "data:closed", this.peers.get(peerId)?.peer);
    }

    async _handleSignal(message) {
      if (!message || message.session_id !== this.sessionId) return;
      const peerId = message.from;
      let state = this.peers.get(peerId);
      if (!state) {
        state = await this._ensurePeer({ participant_id: peerId, display_name: "对方" });
      }
      const pc = state.pc;
      if (message.kind === "description") {
        const description = message.payload;
        const readyForOffer = !state.makingOffer &&
          (pc.signalingState === "stable" || state.isSettingRemoteAnswerPending);
        const offerCollision = description.type === "offer" && !readyForOffer;
        state.ignoreOffer = !state.polite && offerCollision;
        if (state.ignoreOffer) return;

        state.isSettingRemoteAnswerPending = description.type === "answer";
        await pc.setRemoteDescription(description);
        state.isSettingRemoteAnswerPending = false;
        const pendingCandidates = state.pendingCandidates.splice(0);
        for (const candidate of pendingCandidates) {
          await pc.addIceCandidate(candidate);
        }
        if (description.type === "offer") {
          await pc.setLocalDescription();
          await this._sendSignal(peerId, "description", pc.localDescription.toJSON());
        }
      } else if (message.kind === "candidate") {
        if (state.ignoreOffer) return;
        try {
          const type = this._candidateType(message.payload);
          if (type) state.remoteCandidateTypes.add(type);
          this._emitIceDiagnostics(peerId, state);
          if (!pc.remoteDescription) {
            state.pendingCandidates.push(message.payload);
          } else {
            await pc.addIceCandidate(message.payload);
          }
        } catch (error) {
          if (!state.ignoreOffer) throw error;
        }
      }
    }

    _candidateType(candidate) {
      if (!candidate) return null;
      if (typeof candidate.type === "string" && candidate.type) return candidate.type;
      const text = typeof candidate.candidate === "string" ? candidate.candidate : "";
      return text.match(/\styp\s+(host|srflx|prflx|relay)(?:\s|$)/i)?.[1]?.toLowerCase() || null;
    }

    _emitIceDiagnostics(peerId, state) {
      this.onIceDiagnostics(peerId, {
        localCandidateTypes: [...state.localCandidateTypes].sort(),
        remoteCandidateTypes: [...state.remoteCandidateTypes].sort(),
        gatheringComplete: state.iceGatheringComplete,
        restartAttempts: state.iceRestartAttempts,
      }, state.peer);
    }

    _sendSignal(peerId, kind, payload) {
      return new Promise((resolve, reject) => {
        if (!this.socket?.connected || this.closed) return reject(new Error("信令连接已关闭"));
        this.socket.emit("rtc_signal", {
          session_id: this.sessionId,
          to: peerId,
          kind,
          payload,
        }, (response) => {
          if (response?.ok) resolve();
          else reject(new Error(response?.error || "信令发送失败"));
        });
      });
    }

    async connectionInfo(peerId) {
      const pc = this.peers.get(peerId)?.pc;
      if (!pc) return null;
      const stats = await pc.getStats();
      return this._connectionInfoFromStats(stats);
    }

    _connectionInfoFromStats(stats) {
      let pair = null;
      stats.forEach((report) => {
        if (report.type === "transport" && report.selectedCandidatePairId) {
          pair = stats.get(report.selectedCandidatePairId) || pair;
        }
        if (report.type === "candidate-pair" && report.state === "succeeded" && report.nominated) {
          pair = pair || report;
        }
      });
      if (!pair) return null;
      const local = stats.get(pair.localCandidateId);
      const remote = stats.get(pair.remoteCandidateId);
      return {
        localCandidateType: local?.candidateType || "unknown",
        remoteCandidateType: remote?.candidateType || "unknown",
        protocol: local?.protocol || remote?.protocol || "unknown",
        currentRoundTripTime: pair.currentRoundTripTime ?? null,
        availableOutgoingBitrate: pair.availableOutgoingBitrate ?? null,
        availableIncomingBitrate: pair.availableIncomingBitrate ?? null,
        bytesSent: pair.bytesSent ?? null,
        bytesReceived: pair.bytesReceived ?? null,
        relayed: local?.candidateType === "relay" || remote?.candidateType === "relay",
      };
    }

    _sampleRtp(peerId, report) {
      if (!report) return null;
      const key = `${peerId}:${report.id}`;
      const previous = this.statsHistory.get(key);
      const elapsed = previous ? (report.timestamp - previous.timestamp) / 1000 : 0;
      const bytes = report.bytesSent ?? report.bytesReceived ?? null;
      const packets = report.packetsSent ?? report.packetsReceived ?? null;
      const lost = report.packetsLost ?? null;
      const frames = report.framesEncoded ?? report.framesDecoded ?? null;
      const processingTime = report.totalEncodeTime ?? report.totalDecodeTime ?? null;
      let bitrateKbps = null;
      let framesPerSecond = report.framesPerSecond ?? null;
      let lossPercent = null;
      let packetDelta = null;
      let lostDelta = null;
      let processingMsPerFrame = null;

      if (previous && elapsed > 0) {
        if (bytes != null && previous.bytes != null) {
          bitrateKbps = Math.max(0, (bytes - previous.bytes) * 8 / elapsed / 1000);
        }
        if (framesPerSecond == null && frames != null && previous.frames != null) {
          framesPerSecond = Math.max(0, (frames - previous.frames) / elapsed);
        }
        if (
          processingTime != null && previous.processingTime != null
          && frames != null && previous.frames != null && frames > previous.frames
        ) {
          processingMsPerFrame = Math.max(
            0,
            (processingTime - previous.processingTime) * 1000 / (frames - previous.frames),
          );
        }
        if (lost != null && previous.lost != null && packets != null && previous.packets != null) {
          lostDelta = Math.max(0, lost - previous.lost);
          packetDelta = Math.max(0, packets - previous.packets);
          const total = packetDelta + lostDelta;
          if (total > 0) lossPercent = lostDelta / total * 100;
        } else if (packets != null && previous.packets != null) {
          packetDelta = Math.max(0, packets - previous.packets);
        } else if (lost != null && previous.lost != null) {
          lostDelta = Math.max(0, lost - previous.lost);
        }
      }

      this.statsHistory.set(key, {
        timestamp: report.timestamp, bytes, packets, lost, frames, processingTime,
      });
      return {
        bitrateKbps,
        lossPercent,
        jitterMs: report.jitter == null ? null : report.jitter * 1000,
        roundTripTimeMs: report.roundTripTime == null ? null : report.roundTripTime * 1000,
        framesPerSecond,
        width: report.frameWidth ?? null,
        height: report.frameHeight ?? null,
        framesDropped: report.framesDropped ?? null,
        qualityLimitationReason: report.qualityLimitationReason ?? null,
        processingMsPerFrame,
        encoderImplementation: report.encoderImplementation ?? null,
        decoderImplementation: report.decoderImplementation ?? null,
        powerEfficientEncoder: report.powerEfficientEncoder ?? null,
        powerEfficientDecoder: report.powerEfficientDecoder ?? null,
        packetDelta,
        lostDelta,
      };
    }

    async mediaStats(peerId) {
      const state = this.peers.get(peerId);
      if (!state) return null;
      const stats = await state.pc.getStats();
      let outbound = null;
      let inbound = null;
      let remoteInbound = null;
      stats.forEach((report) => {
        const kind = report.kind || report.mediaType;
        if (kind !== "video") return;
        if (report.type === "outbound-rtp" && !report.isRemote) outbound = report;
        if (report.type === "inbound-rtp" && !report.isRemote) inbound = report;
        if (report.type === "remote-inbound-rtp") remoteInbound = report;
      });

      const outboundSample = this._sampleRtp(peerId, outbound);
      const inboundSample = this._sampleRtp(peerId, inbound);
      const remoteSample = this._sampleRtp(peerId, remoteInbound);
      if (outboundSample && outbound?.codecId) {
        outboundSample.codec = stats.get(outbound.codecId)?.mimeType?.split("/", 2)?.[1] || null;
      }
      if (inboundSample && inbound?.codecId) {
        inboundSample.codec = stats.get(inbound.codecId)?.mimeType?.split("/", 2)?.[1] || null;
      }
      if (outboundSample && remoteSample) {
        outboundSample.lossPercent = remoteSample.lossPercent;
        if (
          outboundSample.lossPercent == null
          && remoteSample.lostDelta != null
          && outboundSample.packetDelta > 0
        ) {
          outboundSample.lossPercent = remoteSample.lostDelta / outboundSample.packetDelta * 100;
        }
        outboundSample.jitterMs = remoteSample.jitterMs;
        outboundSample.roundTripTimeMs = remoteSample.roundTripTimeMs;
      }
      const trackSettings = this.localStream?.getVideoTracks()?.[0]?.getSettings?.() || {};
      return {
        connection: this._connectionInfoFromStats(stats),
        outbound: outboundSample,
        inbound: inboundSample,
        capture: {
          width: trackSettings.width ?? null,
          height: trackSettings.height ?? null,
          frameRate: trackSettings.frameRate ?? null,
        },
        profile: this.videoProfiles[state.profileIndex],
      };
    }

    async _applySenderProfile(sender, profileIndex) {
      const profile = this.videoProfiles[profileIndex];
      if (!sender || !profile) return;
      const parameters = sender.getParameters();
      if (!parameters.encodings?.length) return;
      for (const encoding of parameters.encodings) {
        encoding.maxBitrate = profile.maxBitrate;
        encoding.maxFramerate = profile.frameRate;
      }
      try {
        parameters.degradationPreference = "balanced";
        await sender.setParameters(parameters);
      } catch (error) {
        // Some Safari versions reject degradationPreference but accept the
        // bitrate fields. Retry without that optional hint.
        delete parameters.degradationPreference;
        try {
          await sender.setParameters(parameters);
        } catch (_unsupported) {
          return false;
        }
      }
      return true;
    }

    async _applyPeerProfile(state) {
      const videoSender = state?.pc.getSenders().find((sender) => sender.track?.kind === "video");
      return this._applySenderProfile(videoSender, state?.profileIndex);
    }

    async _syncCaptureProfile() {
      const videoTrack = this.localStream?.getVideoTracks()?.[0];
      if (!videoTrack || !this.peers.size) return;
      const targetIndex = Math.min(...[...this.peers.values()].map((state) => state.profileIndex));
      if (targetIndex === this.captureProfileIndex) return;
      const profile = this.videoProfiles[targetIndex];
      await videoTrack.applyConstraints({
        width: { ideal: profile.width },
        height: { ideal: profile.height },
        frameRate: { ideal: profile.frameRate, max: profile.frameRate },
      });
      this.captureProfileIndex = targetIndex;
    }

    async adaptVideo(peerId, metrics) {
      const state = this.peers.get(peerId);
      const outbound = metrics?.outbound;
      if (!state || !outbound) return null;
      const connectionRtt = metrics.connection?.currentRoundTripTime == null
        ? null : metrics.connection.currentRoundTripTime * 1000;
      const rtt = outbound.roundTripTimeMs ?? connectionRtt;
      const bad = (outbound.lossPercent != null && outbound.lossPercent >= 5)
        || (outbound.jitterMs != null && outbound.jitterMs >= 80)
        || (rtt != null && rtt >= 350)
        || ["bandwidth", "cpu"].includes(outbound.qualityLimitationReason);
      const good = outbound.lossPercent != null
        && outbound.lossPercent <= 2
        && (outbound.jitterMs == null || outbound.jitterMs <= 40)
        && (rtt == null || rtt <= 200)
        && !["bandwidth", "cpu"].includes(outbound.qualityLimitationReason);

      state.badSamples = bad ? state.badSamples + 1 : 0;
      state.goodSamples = good ? state.goodSamples + 1 : 0;
      const now = Date.now();
      const cooledDown = now - state.lastProfileChange >= 12000;
      let targetIndex = state.profileIndex;
      if (cooledDown && state.badSamples >= 2 && targetIndex > 0) {
        targetIndex -= 1;
      } else if (cooledDown && state.goodSamples >= 8 && targetIndex < this.videoProfiles.length - 1) {
        targetIndex += 1;
      }
      if (targetIndex === state.profileIndex) return { changed: false, profile: this.videoProfiles[targetIndex] };

      state.profileIndex = targetIndex;
      state.badSamples = 0;
      state.goodSamples = 0;
      state.lastProfileChange = now;
      await this._applyPeerProfile(state);
      await this._syncCaptureProfile();
      return { changed: true, profile: this.videoProfiles[targetIndex] };
    }

    _removePeer(peerId) {
      const state = this.peers.get(peerId);
      if (!state) return;
      state.pc.ontrack = null;
      state.pc.close();
      this.peers.delete(peerId);
      for (const key of [...this.statsHistory.keys()]) {
        if (key.startsWith(`${peerId}:`)) this.statsHistory.delete(key);
      }
    }

    close(notify = true) {
      if (this.closed) return;
      this.closed = true;
      if (notify && this.socket?.connected) this.socket.emit("rtc_leave");
      for (const peerId of [...this.peers.keys()]) this._removePeer(peerId);
      this.socket?.disconnect();
      this.localStream?.getTracks().forEach((track) => track.stop());
    }
  }

  window.RtcClient = RtcClient;
})();
