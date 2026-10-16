"use strict";

// Decorative geography is approximate. Markers use only real nodes from /api/state.
(() => {
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
  const regionLocations = {
    HK:[22,114], MO:[22,113], TW:[24,121], JP:[36,139], SG:[1,104], US:[38,-100],
    KR:[37,127], GB:[54,-2], DE:[51,10], FR:[47,2], CA:[57,-107], AU:[-25,134],
    RU:[60,95], IN:[22,79], NL:[52,5], CN:[35,105],
  };
  const continents = [
    [[-168,70],[-130,72],[-100,80],[-58,55],[-54,48],[-65,42],[-80,25],[-86,16],[-105,21],[-117,32],[-127,50],[-151,59]],
    [[-81,12],[-60,8],[-35,-7],[-40,-22],[-53,-34],[-68,-55],[-76,-32],[-80,-5]],
    [[-18,35],[12,37],[35,30],[51,12],[42,-6],[36,-22],[18,-35],[10,-18],[-5,5],[-17,16]],
    [[-10,36],[-7,58],[15,71],[50,68],[70,75],[110,73],[160,65],[178,52],[150,44],[142,35],[122,22],[109,1],[99,8],[80,9],[65,25],[43,32],[31,42],[10,36]],
    [[113,-22],[130,-11],[145,-13],[155,-28],[143,-39],[119,-34]],
    [[-55,60],[-22,74],[-38,83],[-66,77]],
  ];
  function inside(lon, lat, polygon) {
    let hit = false;
    for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
      const [xi, yi] = polygon[i], [xj, yj] = polygon[j];
      if ((yi > lat) !== (yj > lat) && lon < (xj-xi)*(lat-yi)/(yj-yi)+xi) hit = !hit;
    }
    return hit;
  }
  const land = [];
  for (let lat = -60; lat <= 80; lat += 3.5) {
    for (let lon = -180; lon < 180; lon += 3.5) {
      if (continents.some((polygon) => inside(lon, lat, polygon))) land.push([lat, lon]);
    }
  }
  let requested = true;
  try { requested = localStorage.getItem("route-motion") !== "off"; } catch { /* Private browsing can disable storage. */ }
  let mode = "login", snapshot = null, frame = 0, lastDraw = 0, phase = 0, center = 115;
  const canvases = [...document.querySelectorAll("canvas")].map((element) => ({element, ctx:element.getContext("2d"), width:0, height:0, visible:false})).filter((item) => item.ctx);
  const sizes = new ResizeObserver((entries) => {
    for (const entry of entries) {
      const item = canvases.find((value) => value.element === entry.target);
      if (!item) continue;
      const rect = entry.target.getBoundingClientRect();
      item.width = rect.width; item.height = rect.height;
      const density = Math.min(window.devicePixelRatio || 1, 2);
      item.element.width = Math.round(rect.width * density);
      item.element.height = Math.round(rect.height * density);
      item.ctx.setTransform(density,0,0,density,0,0);
    }
    draw();
    resume();
  });
  const visibility = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      const item = canvases.find((value) => value.element === entry.target);
      if (item) item.visible = entry.isIntersecting;
    }
    resume();
  });
  canvases.forEach((item) => { sizes.observe(item.element); visibility.observe(item.element); });

  function project(lat, lon, angle, radius, cx, cy, lift = 1) {
    const latitude = lat * Math.PI/180, longitude = (lon-angle)*Math.PI/180;
    const x = Math.cos(latitude)*Math.sin(longitude), y = -Math.sin(latitude), z = Math.cos(latitude)*Math.cos(longitude);
    const tilt = -.16;
    return {x:cx+x*radius*lift, y:cy+(y*Math.cos(tilt)-z*Math.sin(tilt))*radius*lift, z:y*Math.sin(tilt)+z*Math.cos(tilt)};
  }
  function paint(item) {
    const {ctx,width:w,height:h,element} = item;
    if (!w || !h) return;
    const login = element.id === "login-globe";
    if ((login && mode !== "login") || (!login && mode !== "app")) return;
    ctx.clearRect(0,0,w,h);
    const radius = Math.min(w*.37,h*.43), cx = w*.50, cy = h*.53;
    const ready = !login && !!snapshot?.core?.running;
    const selected = !login ? snapshot?.nodes?.find((n) => n.id === snapshot.current_node) : null;
    const markers = !login ? [...new Set((snapshot?.nodes || []).filter((n) => n.eligible && regionLocations[n.region]).map((n) => n.region))] : [];
    const rotation = center + (reduce.matches || !requested ? 0 : Math.sin(phase*.14)*12);
    const atmosphere = ctx.createRadialGradient(cx,cy,0,cx,cy,radius*1.25);
    atmosphere.addColorStop(0,"rgba(95,172,117,.035)");
    atmosphere.addColorStop(.75,"rgba(95,172,117,.055)");
    atmosphere.addColorStop(1,"rgba(95,172,117,0)");
    ctx.fillStyle = atmosphere; ctx.fillRect(0,0,w,h);
    ctx.strokeStyle = "rgba(143,212,160,.13)"; ctx.lineWidth = .7;
    ctx.beginPath(); ctx.arc(cx,cy,radius,0,Math.PI*2); ctx.stroke();
    const path = (coordinates) => {
      let started = false;
      ctx.beginPath();
      for (const [lat,lon] of coordinates) {
        const p = project(lat,lon,rotation,radius,cx,cy);
        if (p.z < -.05) { started = false; continue; }
        if (!started) ctx.moveTo(p.x,p.y); else ctx.lineTo(p.x,p.y);
        started = true;
      }
      ctx.stroke();
    };
    ctx.strokeStyle = "rgba(149,204,163,.075)"; ctx.lineWidth = .6;
    for (let lat = -60; lat <= 60; lat += 30) path(Array.from({length:121},(_,i) => [lat,i*3-180]));
    for (let lon = -180; lon < 180; lon += 30) path(Array.from({length:61},(_,i) => [i*3-90,lon]));
    for (const [lat,lon] of land) {
      const p = project(lat,lon,rotation,radius,cx,cy);
      const opacity = .10 + Math.max(0,p.z)*.55;
      ctx.fillStyle = `rgba(146,214,159,${opacity})`;
      ctx.beginPath(); ctx.arc(p.x,p.y,p.z>0 ? 1.05 : .7,0,Math.PI*2); ctx.fill();
    }
    // Orbital strokes and a moving highlight make the network silhouette readable.
    ctx.save(); ctx.translate(cx,cy); ctx.rotate(-.3);
    ctx.strokeStyle = "rgba(161,225,174,.13)"; ctx.lineWidth = .7;
    ctx.beginPath(); ctx.ellipse(0,0,radius*1.32,radius*.42,0,0,Math.PI*2); ctx.stroke();
    ctx.strokeStyle = "rgba(170,244,192,.65)"; ctx.lineWidth = 1.2;
    const orbitAngle = phase*.22;
    ctx.beginPath(); ctx.ellipse(0,0,radius*1.32,radius*.42,0,orbitAngle,orbitAngle+.5); ctx.stroke();
    ctx.restore();
    markers.forEach((code,index) => {
      const position = regionLocations[code], p = project(...position,rotation,radius,cx,cy,1.015);
      if (p.z < 0) return;
      const current = selected?.region === code;
      const pulse = reduce.matches || !requested ? 1 : 1 + Math.sin(phase*2+index)*.25;
      ctx.strokeStyle = current ? "rgba(203,255,216,.85)" : "rgba(167,232,184,.6)";
      ctx.fillStyle = current ? "#d7ffe2" : "#99dbad";
      ctx.beginPath(); ctx.arc(p.x,p.y,current ? 3 : 2,0,Math.PI*2); ctx.fill();
      ctx.beginPath(); ctx.arc(p.x,p.y,(current ? 8 : 5)*pulse,0,Math.PI*2); ctx.lineWidth=.7; ctx.stroke();
      ctx.font = "9px Consolas, monospace"; ctx.fillStyle = current ? "#d7ffe2" : "#aacbb4";
      ctx.fillText(code,p.x+8,p.y-7-index%2*3);
    });
    if (login || ready && markers.length) {
      const angle = phase*.17 + .8;
      ctx.fillStyle = "rgba(188,241,199,.7)";
      ctx.beginPath(); ctx.arc(cx+Math.cos(angle)*radius*1.07,cy+Math.sin(angle)*radius*.99,1.7,0,Math.PI*2); ctx.fill();
    }
  }
  function draw() { canvases.filter((item) => item.visible).forEach(paint); }
  function shouldAnimate() { return requested && !reduce.matches && !document.hidden && canvases.some((item) => item.visible); }
  function tick(timestamp) {
    frame = 0;
    if (!shouldAnimate()) return;
    if (timestamp-lastDraw >= 1000/30) {
      const elapsed = Math.min((timestamp-lastDraw)/1000,.1); lastDraw = timestamp; phase += elapsed;
      const current = snapshot?.nodes?.find((n) => n.id === snapshot.current_node);
      const target = mode === "app" && current && regionLocations[current.region] ? regionLocations[current.region][1] : 115;
      const difference = ((target-center+540)%360)-180;
      center += difference*.035;
      draw();
    }
    frame = requestAnimationFrame(tick);
  }
  function resume() {
    if (frame) cancelAnimationFrame(frame);
    frame = 0; draw();
    if (shouldAnimate()) { lastDraw = performance.now(); frame = requestAnimationFrame(tick); }
  }
  function applyPreference() {
    const enabled = requested && !reduce.matches;
    document.documentElement.dataset.motion = enabled ? "on" : "off";
    const button = document.querySelector("#motion-toggle");
    if (button) {
      button.textContent = enabled ? "动效已开" : "动效已关";
      button.setAttribute("aria-pressed", String(enabled));
      button.disabled = reduce.matches;
      button.title = reduce.matches ? "已跟随系统减少动态效果设置" : "切换动态效果";
    }
    resume();
  }
  document.querySelector("#motion-toggle")?.addEventListener("click", () => {
    requested = !requested;
    try { localStorage.setItem("route-motion", requested ? "on" : "off"); } catch { /* Keep the in-memory preference. */ }
    applyPreference();
  });
  reduce.addEventListener("change", applyPreference);
  document.addEventListener("visibilitychange", resume);
  window.RouteVisuals = {
    mode(value) { mode = value; resume(); },
    update(value) {
      snapshot = value;
      if (!requested || reduce.matches) {
        const current = value.nodes.find((n) => n.id === value.current_node);
        if (current && regionLocations[current.region]) center = regionLocations[current.region][1];
      }
      resume();
    },
    enter(element) {
      if (!element || !requested || reduce.matches) return;
      element.classList.remove("view-enter");
      void element.offsetWidth;
      element.classList.add("view-enter");
    },
    value(element,text) {
      if (!element || element.textContent === String(text)) return;
      element.textContent = text;
      if (!requested || reduce.matches) return;
      element.classList.remove("value-changed");
      void element.offsetWidth;
      element.classList.add("value-changed");
    },
  };
  window.addEventListener("pagehide", () => { if (frame) cancelAnimationFrame(frame); frame=0; });
  window.addEventListener("pageshow", resume);
  applyPreference();
})();
