/*
 * Lightweight original Canvas 2D weather background.
 * Visual direction inspired by Web Weather by greywen (MIT):
 * https://github.com/greywen/web-weather
 */
(function () {
  'use strict';

  const CACHE_KEY = 'site-weather-background-v1';
  const LOCATION_KEY = 'site-weather-location-v1';
  const CACHE_MS = 10 * 60 * 1000;
  // Shared by every path on this origin: page navigation never asks again.
  // Re-check at most weekly, unless the server reports that the network changed.
  const LOCATION_CACHE_MS = 7 * 24 * 60 * 60 * 1000;
  const isMobile = matchMedia('(max-width: 700px), (pointer: coarse)').matches;
  const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;

  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
  const number = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;

  class WeatherBackground {
    constructor() {
      this.width = 0;
      this.height = 0;
      this.dpr = 1;
      this.raf = 0;
      this.lastFrame = 0;
      this.frameInterval = 1000 / (isMobile ? 24 : 30);
      this.weather = 'clear';
      this.isDay = this.localDaylight();
      this.cloudCover = .15;
      this.wind = 0;
      this.precipitation = 0;
      this.snowfall = 0;
      this.visibility = 10000;
      this.rain = [];
      this.snow = [];
      this.clouds = [];
      this.stars = [];
      this.fog = [];
      this.cloudSprites = [];
      this.fogSprite = null;
      this.nextLightning = 0;
      this.resizeTimer = 0;
      this.coordinates = null;
      this.savedLocation = this.readSavedLocation();
      this.networkRefreshTriggered = false;

      this.root = document.createElement('div');
      this.root.id = 'weatherBackground';
      this.root.className = 'weather-background';
      this.root.setAttribute('aria-hidden', 'true');
      this.root.dataset.weather = 'clear';
      this.root.dataset.day = String(this.localDaylight());
      this.root.innerHTML = `
        <div class="weather-background__sun"></div>
        <div class="weather-background__moon"></div>
        <canvas class="weather-background__canvas"></canvas>
        <div class="weather-background__wash"></div>
        <div class="weather-background__flash"></div>`;
      document.body.prepend(this.root);
      document.body.classList.add('weather-active');
      this.applyDayState();
      this.canvas = this.root.querySelector('canvas');
      this.ctx = this.canvas.getContext('2d', { alpha: true });
      this.flash = this.root.querySelector('.weather-background__flash');

      this.resize();
      this.applyCelestialPosition(new Date().getHours() + new Date().getMinutes() / 60);
      this.rebuildScene();
      this.render(performance.now());
      requestAnimationFrame(() => this.root.classList.add('is-ready'));
      this.restoreCache();
      this.locateAndRefresh();
      this.refreshTimer = setInterval(() => {
        if (document.hidden) return;
        if (this.coordinates) this.fetchWeather(this.coordinates.latitude, this.coordinates.longitude);
        else this.fetchWeather();
      }, CACHE_MS);

      addEventListener('resize', () => {
        clearTimeout(this.resizeTimer);
        this.resizeTimer = setTimeout(() => {
          this.resize();
          this.rebuildScene();
          this.render(performance.now());
        }, 160);
      }, { passive: true });
      document.addEventListener('visibilitychange', () => {
        if (document.hidden) this.stop();
        else this.start();
      });
      this.start();
    }

    localDaylight() {
      const hour = new Date().getHours();
      return hour >= 6 && hour < 19;
    }

    applyDayState() {
      document.body.classList.toggle('weather-night', !this.isDay);
      document.documentElement.dataset.weatherDay = String(this.isDay);
    }

    resize() {
      this.width = innerWidth;
      this.height = innerHeight;
      this.dpr = Math.min(devicePixelRatio || 1, isMobile ? 1.2 : 1.5);
      this.canvas.width = Math.round(this.width * this.dpr);
      this.canvas.height = Math.round(this.height * this.dpr);
      this.canvas.style.width = `${this.width}px`;
      this.canvas.style.height = `${this.height}px`;
      this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    }

    start() {
      if (reduceMotion || document.hidden || this.raf) return;
      this.raf = requestAnimationFrame(time => this.loop(time));
    }

    stop() {
      if (this.raf) cancelAnimationFrame(this.raf);
      this.raf = 0;
    }

    loop(time) {
      this.raf = 0;
      if (time - this.lastFrame >= this.frameInterval) {
        const delta = clamp((time - this.lastFrame) / 16.67, .5, 3);
        this.lastFrame = time;
        this.update(delta, time);
        this.render(time);
      }
      this.start();
    }

    weatherType(code, temperature) {
      if ([96, 99].includes(code)) return 'hail';
      if (code === 95) return 'thunder';
      if ([56, 57, 66, 67, 71, 73, 75, 77, 85, 86].includes(code)) return 'snow';
      if ([51, 53, 55, 61, 63, 65, 80, 81, 82].includes(code)) return 'rain';
      if ([45, 48].includes(code)) return 'fog';
      if ([2, 3].includes(code)) return 'cloudy';
      if (temperature < -3 && code === 1) return 'snow';
      return 'clear';
    }

    apply(payload, save = true) {
      if (!payload || !payload.ok || !payload.current) return;
      const current = payload.current;
      const code = number(current.weather_code);
      const temperature = number(current.temperature_2m);
      this.weather = this.weatherType(code, temperature);
      this.isDay = number(current.is_day, this.localDaylight() ? 1 : 0) === 1;
      this.cloudCover = clamp(number(current.cloud_cover, 15) / 100, 0, 1);
      this.precipitation = Math.max(number(current.precipitation), number(current.rain) + number(current.showers));
      this.snowfall = number(current.snowfall);
      this.visibility = number(current.visibility, 10000);
      const windSpeed = number(current.wind_speed_10m);
      const direction = number(current.wind_direction_10m);
      const windSign = direction >= 90 && direction <= 270 ? 1 : -1;
      this.wind = windSign * clamp(windSpeed / 18, .15, 2.4);
      this.root.dataset.weather = this.weather;
      this.root.dataset.day = String(this.isDay);
      this.applyDayState();
      this.root.dataset.source = payload.source || 'default';
      this.root.title = payload.location_name ? `天气背景：${payload.location_name}` : '';

      let hour = new Date().getHours() + new Date().getMinutes() / 60;
      if (typeof current.time === 'string' && current.time.length >= 16) {
        hour = number(current.time.slice(11, 13)) + number(current.time.slice(14, 16)) / 60;
      }
      this.applyCelestialPosition(hour);
      this.rebuildScene();
      this.render(performance.now());
      if (save) {
        try {
          localStorage.setItem(CACHE_KEY, JSON.stringify({ savedAt: Date.now(), payload }));
        } catch (_) {}
      }
    }

    restoreCache() {
      try {
        const cached = JSON.parse(localStorage.getItem(CACHE_KEY) || 'null');
        if (cached && Date.now() - cached.savedAt < CACHE_MS) this.apply(cached.payload, false);
      } catch (_) {}
    }

    readSavedLocation() {
      try {
        const saved = JSON.parse(localStorage.getItem(LOCATION_KEY) || 'null');
        if (!saved || saved.expiresAt < Date.now() || !['device', 'ip'].includes(saved.mode)) return null;
        if (saved.mode === 'device' && (!Number.isFinite(saved.latitude) || !Number.isFinite(saved.longitude))) return null;
        return saved;
      } catch (_) {
        return null;
      }
    }

    saveLocation(location) {
      this.savedLocation = { expiresAt: Date.now() + LOCATION_CACHE_MS, ...location };
      try { localStorage.setItem(LOCATION_KEY, JSON.stringify(this.savedLocation)); } catch (_) {}
    }

    async locateAndRefresh() {
      if (this.savedLocation) {
        if (this.savedLocation.mode === 'device') {
          this.coordinates = {
            latitude: this.savedLocation.latitude,
            longitude: this.savedLocation.longitude,
          };
          await this.fetchWeather(this.coordinates.latitude, this.coordinates.longitude);
        } else {
          await this.fetchWeather();
        }
        return;
      }
      this.requestDeviceLocation();
    }

    requestDeviceLocation() {
      const useIp = () => {
        this.coordinates = null;
        this.saveLocation({ mode: 'ip', networkKey: '' });
        this.fetchWeather();
      };
      if (!isSecureContext || !navigator.geolocation) {
        useIp();
        return;
      }
      navigator.geolocation.getCurrentPosition(
        position => {
          this.coordinates = {
            latitude: position.coords.latitude,
            longitude: position.coords.longitude,
          };
          this.saveLocation({ mode: 'device', ...this.coordinates, networkKey: '' });
          this.fetchWeather(this.coordinates.latitude, this.coordinates.longitude);
        },
        useIp,
        { enableHighAccuracy: true, timeout: 10000, maximumAge: 5 * 60 * 1000 }
      );
    }

    async fetchWeather(latitude, longitude) {
      const query = Number.isFinite(latitude) && Number.isFinite(longitude)
        ? `?lat=${encodeURIComponent(latitude.toFixed(5))}&lon=${encodeURIComponent(longitude.toFixed(5))}`
        : '';
      try {
        const response = await fetch(`/api/weather/background${query}`, {
          credentials: 'same-origin', headers: { Accept: 'application/json' }
        });
        const payload = await response.json();
        if (!response.ok) return;
        const oldNetworkKey = this.savedLocation && this.savedLocation.networkKey;
        const networkChanged = oldNetworkKey && payload.network_key && oldNetworkKey !== payload.network_key;
        if (networkChanged && this.savedLocation.mode === 'device' && !this.networkRefreshTriggered) {
          this.networkRefreshTriggered = true;
          this.savedLocation = null;
          try { localStorage.removeItem(LOCATION_KEY); } catch (_) {}
          this.requestDeviceLocation();
          return;
        }
        if (this.savedLocation) {
          this.saveLocation({ ...this.savedLocation, networkKey: payload.network_key || oldNetworkKey || '' });
        }
        this.apply(payload);
      } catch (_) {
        // The already rendered time-based fallback remains visible.
      }
    }

    applyCelestialPosition(hour) {
      const daylightProgress = clamp((hour - 5.5) / 14, 0, 1);
      const arc = Math.sin(daylightProgress * Math.PI);
      const x = 7 + daylightProgress * 86;
      const y = 37 - arc * 25;
      this.root.style.setProperty('--celestial-x', `${x}%`);
      this.root.style.setProperty('--celestial-y', `${y}%`);
    }

    rebuildScene() {
      const areaFactor = clamp((this.width * this.height) / (390 * 844), .7, 2.4);
      const cloudBase = this.weather === 'clear' ? this.cloudCover * 5 : 4 + this.cloudCover * 8;
      const cloudCount = Math.round(clamp(cloudBase * Math.sqrt(areaFactor), 0, isMobile ? 8 : 14));
      this.clouds = Array.from({ length: cloudCount }, (_, index) => ({
        x: Math.random() * (this.width + 300) - 150,
        y: Math.random() * this.height * .52 - 30,
        width: (120 + Math.random() * 230) * (index % 3 === 0 ? 1.25 : 1),
        speed: (.08 + Math.random() * .16) * (this.wind < 0 ? -1 : 1),
        alpha: .08 + Math.random() * .16 + this.cloudCover * .16,
        phase: Math.random() * Math.PI * 2,
        sprite: index % 3,
      }));
      this.cloudSprites = [0, 1, 2].map(variant => this.makeCloudSprite(variant));

      const rainMax = isMobile ? 135 : 260;
      const rainCount = ['rain', 'thunder', 'hail'].includes(this.weather)
        ? Math.round(clamp(65 + this.precipitation * 38, 65, rainMax)) : 0;
      this.rain = Array.from({ length: rainCount }, () => this.makeRain(true));

      const snowMax = isMobile ? 105 : 190;
      const snowCount = this.weather === 'snow'
        ? Math.round(clamp(45 + this.snowfall * 60, 45, snowMax)) : 0;
      this.snow = Array.from({ length: snowCount }, () => this.makeSnow(true));

      this.stars = Array.from({ length: isMobile ? 42 : 76 }, () => ({
        x: Math.random() * this.width,
        y: Math.random() * this.height * .66,
        size: .4 + Math.random() * 1.35,
        alpha: .24 + Math.random() * .58,
        phase: Math.random() * Math.PI * 2,
      }));
      const fogCount = this.weather === 'fog' ? (isMobile ? 7 : 11) : 0;
      this.fog = Array.from({ length: fogCount }, () => ({
        x: Math.random() * this.width,
        y: Math.random() * this.height,
        radius: 120 + Math.random() * Math.min(280, this.width * .42),
        speed: (Math.random() * .13 + .035) * (Math.random() > .5 ? 1 : -1),
        alpha: .035 + Math.random() * .055,
      }));
      this.fogSprite = fogCount ? this.makeFogSprite() : null;
      if (this.weather === 'thunder' || this.weather === 'hail') {
        this.nextLightning = performance.now() + 2200 + Math.random() * 5500;
      }
    }

    makeRain(spread) {
      const depth = .45 + Math.random() * .9;
      return {
        x: Math.random() * (this.width + 100) - 50,
        y: spread ? Math.random() * this.height : -40,
        speed: (12 + Math.random() * 17) * depth,
        length: (12 + Math.random() * 22) * depth,
        alpha: .14 + depth * .23,
      };
    }

    makeSnow(spread) {
      return {
        x: Math.random() * this.width,
        y: spread ? Math.random() * this.height : -15,
        speed: .45 + Math.random() * 1.25,
        radius: 1.2 + Math.random() * 3.1,
        sway: .35 + Math.random() * 1.1,
        phase: Math.random() * Math.PI * 2,
        alpha: .38 + Math.random() * .55,
      };
    }

    makeCloudSprite(variant) {
      const canvas = document.createElement('canvas');
      canvas.width = 420;
      canvas.height = 170;
      const ctx = canvas.getContext('2d');
      const dark = ['rain', 'thunder', 'hail'].includes(this.weather);
      const gradient = ctx.createLinearGradient(0, 22, 0, 155);
      if (dark) {
        gradient.addColorStop(0, this.isDay ? '#d5dde2' : '#74859a');
        gradient.addColorStop(1, this.isDay ? '#526878' : '#142334');
      } else {
        gradient.addColorStop(0, '#ffffff');
        gradient.addColorStop(1, this.isDay ? '#d8e4ea' : '#667b95');
      }
      ctx.filter = `blur(${isMobile ? 6 : 9}px)`;
      ctx.fillStyle = gradient;
      ctx.beginPath();
      const offset = variant * 9;
      ctx.ellipse(92 + offset, 110, 82, 39, 0, 0, Math.PI * 2);
      ctx.ellipse(190, 77 - offset * .35, 102, 65, 0, 0, Math.PI * 2);
      ctx.ellipse(302 - offset, 106, 98, 45, 0, 0, Math.PI * 2);
      ctx.rect(75, 96, 250, 52);
      ctx.fill();
      ctx.filter = 'none';
      return canvas;
    }

    makeFogSprite() {
      const canvas = document.createElement('canvas');
      canvas.width = 192;
      canvas.height = 192;
      const ctx = canvas.getContext('2d');
      const gradient = ctx.createRadialGradient(96, 96, 4, 96, 96, 94);
      gradient.addColorStop(0, 'rgba(238,243,245,.9)');
      gradient.addColorStop(.55, 'rgba(224,232,235,.42)');
      gradient.addColorStop(1, 'rgba(220,228,232,0)');
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, 192, 192);
      return canvas;
    }

    update(delta, time) {
      for (const cloud of this.clouds) {
        cloud.x += (cloud.speed + this.wind * .045) * delta;
        if (cloud.x > this.width + cloud.width) cloud.x = -cloud.width;
        if (cloud.x < -cloud.width) cloud.x = this.width + cloud.width;
      }
      for (let index = 0; index < this.rain.length; index++) {
        const drop = this.rain[index];
        drop.y += drop.speed * delta;
        drop.x += this.wind * 2.5 * delta;
        if (drop.y > this.height + 45 || drop.x > this.width + 80 || drop.x < -80) {
          this.rain[index] = this.makeRain(false);
          this.rain[index].x = Math.random() * (this.width + 120) - 60;
        }
      }
      for (let index = 0; index < this.snow.length; index++) {
        const flake = this.snow[index];
        flake.y += flake.speed * delta;
        flake.x += (Math.sin(time * .0007 + flake.phase) * flake.sway + this.wind * .22) * delta;
        if (flake.y > this.height + 12 || flake.x > this.width + 20 || flake.x < -20) {
          this.snow[index] = this.makeSnow(false);
        }
      }
      for (const puff of this.fog) {
        puff.x += (puff.speed + this.wind * .018) * delta;
        if (puff.x > this.width + puff.radius) puff.x = -puff.radius;
        if (puff.x < -puff.radius) puff.x = this.width + puff.radius;
      }
      if ((this.weather === 'thunder' || this.weather === 'hail') && time > this.nextLightning) {
        this.strikeLightning();
        this.nextLightning = time + 4500 + Math.random() * 9000;
      }
    }

    render(time) {
      const ctx = this.ctx;
      ctx.clearRect(0, 0, this.width, this.height);
      if (!this.isDay) this.drawStars(ctx, time);
      this.drawClouds(ctx, time);
      if (this.weather === 'fog') this.drawFog(ctx);
      if (this.rain.length) this.drawRain(ctx);
      if (this.weather === 'hail') this.drawHail(ctx, time);
      if (this.snow.length) this.drawSnow(ctx, time);
    }

    drawStars(ctx, time) {
      ctx.fillStyle = '#f2f7ff';
      for (const star of this.stars) {
        ctx.globalAlpha = star.alpha * (.72 + Math.sin(time * .001 + star.phase) * .28);
        ctx.beginPath();
        ctx.arc(star.x, star.y, star.size, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
    }

    drawClouds(ctx, time) {
      for (const cloud of this.clouds) {
        const y = cloud.y + Math.sin(time * .00018 + cloud.phase) * 4;
        const w = cloud.width;
        const h = w * .34;
        ctx.globalAlpha = cloud.alpha;
        ctx.drawImage(this.cloudSprites[cloud.sprite], cloud.x, y, w, h);
      }
      ctx.globalAlpha = 1;
    }

    drawRain(ctx) {
      const slant = this.wind * 6;
      ctx.lineWidth = isMobile ? .9 : 1.1;
      ctx.lineCap = 'round';
      ctx.strokeStyle = 'rgba(205,225,246,.34)';
      ctx.beginPath();
      for (const drop of this.rain) {
        ctx.moveTo(drop.x, drop.y);
        ctx.lineTo(drop.x + slant, drop.y + drop.length);
      }
      ctx.stroke();
    }

    drawSnow(ctx, time) {
      ctx.globalAlpha = .78;
      ctx.fillStyle = '#f8fbff';
      ctx.beginPath();
      for (const flake of this.snow) {
        const radius = flake.radius * (1 + Math.sin(time * .001 + flake.phase) * .08);
        ctx.moveTo(flake.x + radius, flake.y);
        ctx.arc(flake.x, flake.y, radius, 0, Math.PI * 2);
      }
      ctx.fill();
      ctx.globalAlpha = 1;
    }

    drawFog(ctx) {
      const density = clamp(1 - this.visibility / 10000, .25, .9);
      for (const puff of this.fog) {
        ctx.globalAlpha = puff.alpha * density * 2.4;
        ctx.drawImage(this.fogSprite, puff.x - puff.radius, puff.y - puff.radius, puff.radius * 2, puff.radius * 2);
      }
      ctx.globalAlpha = 1;
    }

    drawHail(ctx, time) {
      ctx.fillStyle = 'rgba(239,248,255,.78)';
      const count = isMobile ? 22 : 38;
      for (let index = 0; index < count; index++) {
        const x = (index * 83 + time * (.08 + index % 3 * .02)) % (this.width + 40) - 20;
        const y = (index * 131 + time * (.18 + index % 4 * .025)) % (this.height + 40) - 20;
        ctx.beginPath();
        ctx.arc(x, y, 2.2 + index % 3, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    strikeLightning() {
      this.flash.classList.remove('is-flashing');
      void this.flash.offsetWidth;
      this.flash.classList.add('is-flashing');
    }
  }

  function boot() {
    if (!document.body || document.getElementById('weatherBackground')) return;
    window.siteWeatherBackground = new WeatherBackground();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, { once: true });
  else boot();
})();
