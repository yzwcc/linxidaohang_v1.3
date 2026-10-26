from __future__ import annotations

import argparse
import html
import json
import math
import mimetypes
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from robot_local_navigation_v3.config_store import UnifiedConfigStore
from robot_local_navigation_v3.linkcraft_resources import (
    DEFAULT_RESOURCE_CONFIG_PATH,
    discover_linkcraft_resources,
)
from robot_local_navigation_v3.map_point_store import MapPointStore
from robot_local_navigation_v3.models import AppSettings, MapConfig, NavPoint, UnifiedNavConfig


HTML_PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>robot_local_navigation_v3 控制台</title>
  <style>
    body { margin: 0; font-family: Arial, sans-serif; background: #f5f5f5; color: #222; }
    .layout { display: grid; grid-template-columns: 360px 1fr 340px; gap: 16px; padding: 16px; box-sizing: border-box; min-height: 100vh; }
    .panel, .map-card { background: #fff; border-radius: 12px; box-shadow: 0 0 8px rgba(0,0,0,0.08); padding: 16px; }
    .panel { overflow-y: auto; max-height: calc(100vh - 32px); }
    h1 { font-size: 20px; margin: 0 0 12px; }
    h2 { font-size: 16px; margin: 18px 0 8px; }
    label { display: block; font-weight: bold; margin: 10px 0 6px; }
    input, button, select, textarea { width: 100%; box-sizing: border-box; padding: 8px; }
    button { cursor: pointer; margin-top: 8px; }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .hint { color: #666; font-size: 13px; line-height: 1.4; }
    .status { color: #005a9c; white-space: pre-wrap; margin-top: 8px; min-height: 20px; }
    .error { color: #b00020; }
    #mapCanvas { display: block; max-width: 100%; cursor: crosshair; }
    .map-wrap { overflow: auto; }
    .point-item, .map-item, .task-step { border-bottom: 1px solid #eee; padding: 8px 0; font-size: 14px; }
    .task-step .row { grid-template-columns: 1fr 42px 42px 56px; align-items: center; }
    .small { font-size: 12px; color: #666; line-height: 1.5; }
    @media (max-width: 1100px) { .layout { grid-template-columns: 1fr; } .panel { max-height: none; } }
  </style>
</head>
<body>
<div class="layout">
  <div class="panel">
    <h1>地图标点</h1>
    <label for="mapRoot">SLAM 地图根目录</label>
    <input id="mapRoot" value="__DEFAULT_MAP_ROOT__">
    <button id="discoverBtn">发现地图</button>
    <div id="maps"></div>

    <h2>点位参数</h2>
    <label for="pointName">点位名</label>
    <input id="pointName" placeholder="例如：办公室A点">
    <label for="aliases">别名，逗号分隔</label>
    <input id="aliases" placeholder="例如：A点,办公室">
    <div class="row">
      <div>
        <label for="pointX">X</label>
        <input id="pointX" readonly>
      </div>
      <div>
        <label for="pointY">Y</label>
        <input id="pointY" readonly>
      </div>
    </div>
    <div class="row">
      <div>
        <label for="pointZ">Z</label>
        <input id="pointZ" value="0.0">
      </div>
      <div>
        <label for="pointYaw">朝向 Yaw（度）</label>
        <input id="pointYaw" readonly>
      </div>
    </div>
    <div class="row">
      <div>
        <label for="radius">到达半径（到点判定）</label>
        <input id="radius" value="0.15">
      </div>
      <div>
        <label for="maxSpeed">最大速度</label>
        <input id="maxSpeed" value="0.3">
      </div>
    </div>
    <div class="row">
      <div>
        <label for="pncMode">pnc_mode</label>
        <input id="pncMode" value="0">
      </div>
      <div>
        <label for="taskType">task_type</label>
        <input id="taskType" value="2">
      </div>
    </div>
    <label for="arrivalTts">到达后 TTS，可为空</label>
    <textarea id="arrivalTts" rows="2" placeholder="例如：已到达办公室A点"></textarea>
    <div class="row">
      <div>
        <label for="arrivalMotion">到达动作</label>
        <select id="arrivalMotion">
          <option value="">无动作</option>
          <option value="random_speech">讲话随机动作</option>
          <option value="empty_motion">空动作（打断当前动作）</option>
          <option value="raise_hand">raise_hand</option>
          <option value="wave">wave</option>
          <option value="handshake">handshake</option>
          <option value="flying_kiss">flying_kiss</option>
          <option value="clap">clap</option>
          <option value="turn_wave">turn_wave</option>
          <option value="bow">bow</option>
          <option value="like">like</option>
          <option value="ye">ye</option>
          <option value="sweetheart">sweetheart</option>
          <option value="salute">salute</option>
          <option value="sad">sad</option>
          <option value="lightwave">lightwave</option>
          <option value="hug">hug</option>
          <option value="handx">handx</option>
          <option value="chestwave">chestwave</option>
          <option value="cheer">cheer</option>
          <option value="blowkiss">blowkiss</option>
          <option value="bassdance1">bassdance1</option>
          <option value="bassdance2">bassdance2</option>
          <option value="hitclap">hitclap</option>
          <option value="speak">speak</option>
          <option value="clipfist">clipfist</option>
          <option value="photo">photo</option>
          <option value="photo_triple">photo_triple</option>
          <option value="nod">nod</option>
          <option value="shake_head">shake_head</option>
        </select>
      </div>
      <div>
        <label for="arrivalMotionArea">控制区域</label>
        <select id="arrivalMotionArea">
          <option value="right_hand">right_hand</option>
          <option value="left_hand">left_hand</option>
          <option value="head">head</option>
          <option value="waist">waist</option>
          <option value="none">none</option>
        </select>
      </div>
    </div>
    <div class="row">
      <div>
        <label for="arrivalEmoji">到达表情</label>
        <select id="arrivalEmoji">
          <option value="">无表情</option>
          <option value="blink">blink</option>
          <option value="calm">calm</option>
          <option value="calm2">calm2</option>
          <option value="game">game</option>
          <option value="cute">cute</option>
          <option value="cute2">cute2</option>
          <option value="cute3">cute3</option>
          <option value="cute4">cute4</option>
          <option value="eye_close">eye_close</option>
          <option value="eye_open">eye_open</option>
          <option value="boring">boring</option>
          <option value="abnormal">abnormal</option>
          <option value="sleepy">sleepy</option>
          <option value="happy">happy</option>
          <option value="extreme_happy">extreme_happy</option>
          <option value="extreme_happy2">extreme_happy2</option>
          <option value="sad">sad</option>
          <option value="sympathy">sympathy</option>
          <option value="confuse">confuse</option>
          <option value="shock">shock</option>
          <option value="act_cute">act_cute</option>
          <option value="serious">serious</option>
          <option value="thinking">thinking</option>
          <option value="angry">angry</option>
          <option value="extreme_angry">extreme_angry</option>
          <option value="adore">adore</option>
          <option value="extreme_adore">extreme_adore</option>
          <option value="charge">charge</option>
        </select>
      </div>
      <div>
        <label for="arrivalEmojiMode">表情模式</label>
        <select id="arrivalEmojiMode">
          <option value="once">once</option>
          <option value="loop">loop</option>
        </select>
      </div>
    </div>
    <label>
      <input id="isStartPoint" type="checkbox" style="width: auto;">
      设为本地图起点
    </label>
    <button id="resetYawBtn">重置朝向为 0°</button>
    <button id="savePointBtn">保存点位</button>
    <div class="hint">第一次左键选择点位位置，第二次左键设置朝向；右键也可设置朝向。</div>
    <div id="status" class="status"></div>
  </div>

  <div class="map-wrap">
    <div class="map-card">
      <canvas id="mapCanvas"></canvas>
    </div>
  </div>

  <div class="panel">
    <h1>v3 导航桥接</h1>
    <h2>全局路径安全</h2>
    <div class="hint">此处与点位“到达半径”分开。真实全局导航按静态地图规划安全参考线；机器人外接半径必须实测填写，未填写时会拒绝下发 task_type=2、pnc_mode=0 导航。</div>
    <label for="robotRadius">机器人实测外接半径（米）</label>
    <input id="robotRadius" type="number" min="0.01" max="2" step="0.01" placeholder="测量后填写">
    <label for="extraClearance">额外障碍净空（米）</label>
    <input id="extraClearance" type="number" min="0" max="2" step="0.01" value="0.10">
    <label for="occupiedThreshold">地图占用灰度阈值</label>
    <input id="occupiedThreshold" type="number" min="1" max="254" step="1" value="205">
    <label for="navMode">导航模式</label>
    <select id="navMode">
      <option value="dry-run">dry-run</option>
      <option value="execute">execute</option>
    </select>
    <button id="saveSettingsBtn">保存模式</button>
    <div class="row">
      <button id="startBridgeBtn">启动桥接</button>
      <button id="stopBridgeBtn">停止桥接</button>
    </div>
    <button id="refreshBridgeBtn">刷新桥接状态</button>
    <div id="bridgeStatus" class="status"></div>

    <h1>重定位</h1>
    <div class="row">
      <div>
        <label for="relocMapId">map_id</label>
        <input id="relocMapId" placeholder="例如：1779089472769">
      </div>
      <div>
        <label for="relocTimeout">超时秒数</label>
        <input id="relocTimeout" value="30">
      </div>
    </div>
    <div class="row">
      <div>
        <label for="relocX">像素 X</label>
        <input id="relocX">
      </div>
      <div>
        <label for="relocY">像素 Y</label>
        <input id="relocY">
      </div>
    </div>
    <div class="row">
      <div>
        <label for="relocZ">Z</label>
        <input id="relocZ" value="0.0">
      </div>
      <div>
        <label for="relocQw">QW</label>
        <input id="relocQw" value="1.0">
      </div>
    </div>
    <div class="row">
      <div>
        <label for="relocQx">QX</label>
        <input id="relocQx" value="0.0">
      </div>
      <div>
        <label for="relocQy">QY</label>
        <input id="relocQy" value="0.0">
      </div>
    </div>
    <label for="relocQz">QZ</label>
    <input id="relocQz" value="0.0">
    <button id="fillRelocFromPointBtn">使用当前选点像素坐标</button>
    <button id="startRelocBtn">开始重定位</button>
    <button id="refreshRelocBtn">刷新重定位状态</button>
    <div id="relocStatus" class="status"></div>

    <h1>当前点位</h1>
    <div class="small" id="configPath"></div>
    <div id="points"></div>

    <h1>讲解任务编排</h1>
    <label for="taskName">任务名称</label>
    <input id="taskName" value="默认讲解任务">
    <label>
      <input id="taskEnabled" type="checkbox" style="width: auto;" checked>
      启用任务
    </label>
    <label for="taskPointSelect">添加点位</label>
    <select id="taskPointSelect"></select>
    <button id="addTaskStepBtn">添加到任务</button>
    <button id="refreshLinkcraftBtn">刷新灵创舞蹈/动作</button>
    <div id="taskSteps"></div>
    <button id="saveTaskBtn">保存任务编排</button>
    <div id="taskStatus" class="status"></div>
  </div>
</div>

<script>
const mapRootInput = document.getElementById("mapRoot");
const mapsDiv = document.getElementById("maps");
const pointsDiv = document.getElementById("points");
const canvas = document.getElementById("mapCanvas");
const ctx = canvas.getContext("2d");
const image = new Image();
const statusEl = document.getElementById("status");
const pointNameInput = document.getElementById("pointName");
const aliasesInput = document.getElementById("aliases");
const pointXInput = document.getElementById("pointX");
const pointYInput = document.getElementById("pointY");
const pointZInput = document.getElementById("pointZ");
const pointYawInput = document.getElementById("pointYaw");
const radiusInput = document.getElementById("radius");
const maxSpeedInput = document.getElementById("maxSpeed");
const pncModeInput = document.getElementById("pncMode");
const taskTypeInput = document.getElementById("taskType");
const arrivalTtsInput = document.getElementById("arrivalTts");
const arrivalMotionInput = document.getElementById("arrivalMotion");
const arrivalMotionAreaInput = document.getElementById("arrivalMotionArea");
const arrivalEmojiInput = document.getElementById("arrivalEmoji");
const arrivalEmojiModeInput = document.getElementById("arrivalEmojiMode");
const isStartPointInput = document.getElementById("isStartPoint");
const configPathEl = document.getElementById("configPath");
const navModeInput = document.getElementById("navMode");
const robotRadiusInput = document.getElementById("robotRadius");
const extraClearanceInput = document.getElementById("extraClearance");
const occupiedThresholdInput = document.getElementById("occupiedThreshold");
const bridgeStatusEl = document.getElementById("bridgeStatus");
const relocMapIdInput = document.getElementById("relocMapId");
const relocTimeoutInput = document.getElementById("relocTimeout");
const relocXInput = document.getElementById("relocX");
const relocYInput = document.getElementById("relocY");
const relocZInput = document.getElementById("relocZ");
const relocQxInput = document.getElementById("relocQx");
const relocQyInput = document.getElementById("relocQy");
const relocQzInput = document.getElementById("relocQz");
const relocQwInput = document.getElementById("relocQw");
const relocStatusEl = document.getElementById("relocStatus");
const taskNameInput = document.getElementById("taskName");
const taskEnabledInput = document.getElementById("taskEnabled");
const taskPointSelect = document.getElementById("taskPointSelect");
const taskStepsDiv = document.getElementById("taskSteps");
const taskStatusEl = document.getElementById("taskStatus");

let currentMap = null;
let currentPoints = [];
let currentTourTask = {name: "默认讲解任务", enabled: true, steps: []};
let currentLinkcraftResources = [];
let targetPixel = null;
let headingPixel = null;
let awaitingHeading = false;
let currentYaw = 0;
let editingPointId = null;

const defaultMotionAreas = [
  ["right_hand", "右手"],
  ["left_hand", "左手"],
  ["head", "头部"],
  ["waist", "腰部"],
  ["none", "无指定区域"]
];

function syncArrivalMotionArea(preferredArea = "") {
  const motion = arrivalMotionInput.value;
  const options = motion === "empty_motion"
    ? [["0", "域 0（空动作）"]]
    : motion === "random_speech"
      ? Array.from({length: 28}, (_, index) => {
          const value = String(index + 21);
          return [value, `域 ${value}`];
        })
      : defaultMotionAreas;
  const current = preferredArea || arrivalMotionAreaInput.value;
  arrivalMotionAreaInput.innerHTML = "";
  options.forEach(([value, label]) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    arrivalMotionAreaInput.appendChild(option);
  });
  arrivalMotionAreaInput.value = options.some(([value]) => value === current)
    ? current
    : options[0][0];
  arrivalMotionAreaInput.disabled = !motion;
}

arrivalMotionInput.addEventListener("change", () => syncArrivalMotionArea());
syncArrivalMotionArea("right_hand");

function setStatus(text, error=false) {
  statusEl.textContent = text;
  statusEl.className = error ? "status error" : "status";
}

function setBridgeStatus(text, error=false) {
  bridgeStatusEl.textContent = text;
  bridgeStatusEl.className = error ? "status error" : "status";
}

function setRelocStatus(text, error=false) {
  relocStatusEl.textContent = text;
  relocStatusEl.className = error ? "status error" : "status";
}

function setTaskStatus(text, error=false) {
  taskStatusEl.textContent = text;
  taskStatusEl.className = error ? "status error" : "status";
}

function worldToPixel(point) {
  return {
    u: point.x * currentMap.resolution_pixels_per_meter + currentMap.origin_u,
    v: currentMap.origin_v - point.y * currentMap.resolution_pixels_per_meter
  };
}

function pixelToWorld(u, v) {
  return {
    x: (u - currentMap.origin_u) / currentMap.resolution_pixels_per_meter,
    y: -(v - currentMap.origin_v) / currentMap.resolution_pixels_per_meter
  };
}

function radiansToDegrees(rad) {
  return rad * 180 / Math.PI;
}

function normalizeAngle(angle) {
  let value = angle;
  while (value <= -Math.PI) value += Math.PI * 2;
  while (value > Math.PI) value -= Math.PI * 2;
  return value;
}

function setCurrentYaw(yaw) {
  currentYaw = normalizeAngle(yaw);
  pointYawInput.value = radiansToDegrees(currentYaw).toFixed(1);
}

function quaternionToYaw(point) {
  const orientation = point.pose && point.pose.orientation ? point.pose.orientation : {};
  const qx = Number(orientation.x || 0);
  const qy = Number(orientation.y || 0);
  const qz = Number(orientation.z || 0);
  const qw = Number(orientation.w || 1);
  const siny = 2 * (qw * qz + qx * qy);
  const cosy = 1 - 2 * (qy * qy + qz * qz);
  return Math.atan2(siny, cosy);
}

function drawArrow(startU, startV, yaw, color, length=28) {
  const endU = startU + length * Math.cos(yaw);
  const endV = startV - length * Math.sin(yaw);
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(startU, startV);
  ctx.lineTo(endU, endV);
  ctx.stroke();
  const headAngle = Math.atan2(startV - endV, endU - startU);
  ctx.beginPath();
  ctx.moveTo(endU, endV);
  ctx.lineTo(endU - 8 * Math.cos(headAngle - Math.PI / 6), endV + 8 * Math.sin(headAngle - Math.PI / 6));
  ctx.lineTo(endU - 8 * Math.cos(headAngle + Math.PI / 6), endV + 8 * Math.sin(headAngle + Math.PI / 6));
  ctx.closePath();
  ctx.fill();
}

function resetPlacementState() {
  targetPixel = null;
  headingPixel = null;
  awaitingHeading = false;
  editingPointId = null;
}

function resetPointFields() {
  pointXInput.value = "";
  pointYInput.value = "";
  pointZInput.value = "0.0";
  arrivalTtsInput.value = "";
  arrivalMotionInput.value = "";
  syncArrivalMotionArea("right_hand");
  arrivalEmojiInput.value = "";
  arrivalEmojiModeInput.value = "once";
  isStartPointInput.checked = false;
  setCurrentYaw(0);
}

function drawMap() {
  if (!image.src) return;
  canvas.width = image.width;
  canvas.height = image.height;
  ctx.drawImage(image, 0, 0);
  ctx.font = "14px Arial";
  currentPoints.forEach((point) => {
    const p = worldToPixel(point.pose.position);
    ctx.fillStyle = "rgba(255,0,0,0.85)";
    ctx.beginPath();
    ctx.arc(p.u, p.v, 5, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#111";
    ctx.fillText(point.name, p.u + 8, p.v - 8);
    drawArrow(p.u, p.v, quaternionToYaw(point), "#ff9800");
  });
  if (targetPixel) {
    ctx.strokeStyle = "#00c2ff";
    ctx.lineWidth = 2;
    ctx.strokeRect(targetPixel.u - 6, targetPixel.v - 6, 12, 12);
    drawArrow(targetPixel.u, targetPixel.v, currentYaw, "#00c2ff");
  }
  if (targetPixel && headingPixel) {
    ctx.strokeStyle = "rgba(0,120,255,0.9)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(targetPixel.u, targetPixel.v);
    ctx.lineTo(headingPixel.u, headingPixel.v);
    ctx.stroke();
  }
}

async function discoverMaps() {
  const resp = await fetch(`/api/discover_maps?map_root=${encodeURIComponent(mapRootInput.value)}`);
  const payload = await resp.json();
  if (!resp.ok) {
    setStatus(payload.error || "发现地图失败", true);
    return;
  }
  mapsDiv.innerHTML = "";
  payload.maps.forEach((map) => {
    const item = document.createElement("div");
    item.className = "map-item";
    const btn = document.createElement("button");
    btn.textContent = `加载 ${map.map_name}`;
    btn.onclick = () => loadMap(map.map_name);
    item.appendChild(btn);
    mapsDiv.appendChild(item);
  });
}

async function loadMap(mapName) {
  const resp = await fetch(`/api/map?map_root=${encodeURIComponent(mapRootInput.value)}&map_name=${encodeURIComponent(mapName)}`);
  const payload = await resp.json();
  if (!resp.ok) {
    setStatus(payload.error || "加载地图失败", true);
    return;
  }
  currentMap = payload.map;
  currentPoints = payload.points || [];
  currentTourTask = payload.tour_task || {name: "默认讲解任务", enabled: true, steps: []};
  await loadLinkcraftResources(false);
  relocMapIdInput.value = String(currentMap.map_id);
  relocXInput.value = String(currentMap.origin_u);
  relocYInput.value = String(currentMap.origin_v);
  relocZInput.value = "0.0";
  relocQxInput.value = "0.0";
  relocQyInput.value = "0.0";
  relocQzInput.value = "0.0";
  relocQwInput.value = "1.0";
  resetPlacementState();
  resetPointFields();
  image.onload = drawMap;
  image.src = `/api/map_image?map_root=${encodeURIComponent(mapRootInput.value)}&map_name=${encodeURIComponent(mapName)}&t=${Date.now()}`;
  renderPoints();
  renderTaskEditor();
  setStatus(`已加载地图 ${mapName}，已从 grid_map_info.txt 填入地图原点像素 x=${currentMap.origin_u}, y=${currentMap.origin_v}`);
}

function getArrivalActions(point) {
  return point.business && Array.isArray(point.business.on_arrival) ? point.business.on_arrival : [];
}

function describeArrivalActions(point) {
  const parts = [];
  getArrivalActions(point).forEach((action) => {
    if (!action || !action.type) return;
    if (action.type === "motion" && action.motion) {
      parts.push(`动作=${action.motion}@${action.area || "right_hand"}`);
      return;
    }
    if (action.type === "emoji" && action.emotion) {
      parts.push(`表情=${action.emotion}/${action.mode || "once"}`);
      return;
    }
    if (action.type === "tts" && action.text) {
      parts.push("TTS");
    }
  });
  return parts.length ? `<br>${parts.join("，")}` : "";
}

function renderPoints() {
  pointsDiv.innerHTML = "";
  currentPoints.forEach((point) => {
    const div = document.createElement("div");
    div.className = "point-item";
    const pos = point.pose.position;
    const yaw = radiansToDegrees(quaternionToYaw(point)).toFixed(1);
    const radius = Number(point.navigation.target_pose_radius ?? 0.15).toFixed(2);
    const speed = Number(point.navigation.max_forward_speed ?? 0.3).toFixed(2);
    const startLabel = point.is_start ? ` <span class="small">[起点]</span>` : "";
    div.innerHTML = `<b>${point.name}</b>${startLabel}<div class="small">${point.id}<br>x=${pos.x.toFixed(3)}, y=${pos.y.toFixed(3)}, yaw=${yaw}° r=${radius} v=${speed}${describeArrivalActions(point)}</div>`;
    const editBtn = document.createElement("button");
    editBtn.textContent = "编辑";
    editBtn.onclick = () => editPoint(point);
    const delBtn = document.createElement("button");
    delBtn.textContent = "删除";
    delBtn.onclick = async () => {
      const resp = await fetch(`/api/points/${encodeURIComponent(point.id)}`, {method: "DELETE"});
      if (!resp.ok) {
        const payload = await resp.json();
        setStatus(payload.error || "删除失败", true);
        return;
      }
      await loadMap(currentMap.map_name);
      setStatus(`已删除点位：${point.name}`);
    };
    div.appendChild(editBtn);
    div.appendChild(delBtn);
    pointsDiv.appendChild(div);
  });
}

function pointById(pointId) {
  return currentPoints.find((point) => point.id === pointId) || null;
}

function normalizeTaskSteps() {
  const steps = Array.isArray(currentTourTask.steps) ? currentTourTask.steps : [];
  const validIds = new Set(currentPoints.map((point) => point.id));
  return steps
    .map((step) => {
      if (typeof step === "string") return {point_id: step, enabled: true};
      const source = step && typeof step === "object" ? step : {};
      return {
        ...source,
        point_id: source.point_id || source.id || "",
        enabled: source.enabled !== false
      };
    })
    .filter((step) => step.point_id && validIds.has(step.point_id));
}

function renderTaskEditor() {
  currentTourTask = {
    name: currentTourTask.name || "默认讲解任务",
    enabled: currentTourTask.enabled !== false,
    steps: normalizeTaskSteps()
  };
  taskNameInput.value = currentTourTask.name;
  taskEnabledInput.checked = currentTourTask.enabled;
  taskPointSelect.innerHTML = "";
  currentPoints.forEach((point) => {
    const option = document.createElement("option");
    option.value = point.id;
    option.textContent = point.name;
    taskPointSelect.appendChild(option);
  });
  taskStepsDiv.innerHTML = "";
  currentTourTask.steps.forEach((step, index) => {
    const point = pointById(step.point_id);
    const div = document.createElement("div");
    div.className = "task-step";
    const row = document.createElement("div");
    row.className = "row";
    const label = document.createElement("div");
    label.innerHTML = `<b>${index + 1}. ${point ? point.name : step.point_id}</b><div class="small">${step.point_id}</div>`;
    const upBtn = document.createElement("button");
    upBtn.textContent = "↑";
    upBtn.disabled = index === 0;
    upBtn.onclick = () => moveTaskStep(index, -1);
    const downBtn = document.createElement("button");
    downBtn.textContent = "↓";
    downBtn.disabled = index === currentTourTask.steps.length - 1;
    downBtn.onclick = () => moveTaskStep(index, 1);
    const delBtn = document.createElement("button");
    delBtn.textContent = "删除";
    delBtn.onclick = () => removeTaskStep(index);
    row.appendChild(label);
    row.appendChild(upBtn);
    row.appendChild(downBtn);
    row.appendChild(delBtn);
    div.appendChild(row);

    const linkcraftLabel = document.createElement("label");
    linkcraftLabel.textContent = "到点内容完成后执行灵创舞蹈/动作";
    const linkcraftSelect = document.createElement("select");
    const noActionOption = document.createElement("option");
    noActionOption.value = "";
    noActionOption.textContent = "不执行，继续下一点";
    linkcraftSelect.appendChild(noActionOption);
    currentLinkcraftResources.filter((resource) => resource.available !== false).forEach((resource) => {
      const option = document.createElement("option");
      option.value = resource.resource_key;
      option.textContent = `${resource.name}（约 ${Number(resource.duration_sec || 30).toFixed(1)} 秒）`;
      linkcraftSelect.appendChild(option);
    });
    const selectedAction = step.linkcraft_action || null;
    if (selectedAction && ![...linkcraftSelect.options].some((option) => option.value === selectedAction.resource_key)) {
      const missingOption = document.createElement("option");
      missingOption.value = selectedAction.resource_key;
      missingOption.textContent = `${selectedAction.name || selectedAction.resource_key}（当前机器人未找到）`;
      linkcraftSelect.appendChild(missingOption);
    }
    linkcraftSelect.value = selectedAction ? selectedAction.resource_key : "";
    linkcraftSelect.onchange = () => {
      const resource = currentLinkcraftResources.find((item) => item.resource_key === linkcraftSelect.value);
      step.linkcraft_action = resource ? {
        resource_key: resource.resource_key,
        resource_version: resource.resource_version,
        name: resource.name,
        resource_type: resource.resource_type,
        duration_sec: Number(resource.duration_sec || 30)
      } : null;
      renderTaskEditor();
    };
    linkcraftLabel.appendChild(linkcraftSelect);
    div.appendChild(linkcraftLabel);

    const durationLabel = document.createElement("label");
    durationLabel.textContent = "预计执行时长（秒）";
    const durationInput = document.createElement("input");
    durationInput.type = "number";
    durationInput.min = "0.1";
    durationInput.max = "300";
    durationInput.step = "0.1";
    durationInput.value = Number(selectedAction && selectedAction.duration_sec || 30).toFixed(1);
    durationInput.disabled = !selectedAction;
    durationInput.onchange = () => {
      if (!step.linkcraft_action) return;
      const value = Number(durationInput.value);
      step.linkcraft_action.duration_sec = Math.min(300, Math.max(0.1, Number.isFinite(value) ? value : 30));
      durationInput.value = Number(step.linkcraft_action.duration_sec).toFixed(1);
    };
    durationLabel.appendChild(durationInput);
    div.appendChild(durationLabel);
    taskStepsDiv.appendChild(div);
  });
}

async function loadLinkcraftResources(showStatus = true) {
  const resourceUrl = `http://${window.location.hostname}:3000/api/robots/default/linkcraft/resources?refresh=1&t=${Date.now()}`;
  const resp = await fetch(resourceUrl);
  const payload = await resp.json();
  if (!resp.ok) {
    currentLinkcraftResources = [];
    if (showStatus) setTaskStatus(payload.error || "读取灵创资源失败", true);
    return;
  }
  currentLinkcraftResources = Array.isArray(payload.resources) ? payload.resources : [];
  if (showStatus) {
    setTaskStatus(`已读取 ${currentLinkcraftResources.length} 个灵创舞蹈/动作，不会自动执行`);
    renderTaskEditor();
  }
}

function addTaskStep() {
  if (!currentMap) {
    setTaskStatus("请先加载地图", true);
    return;
  }
  const pointId = taskPointSelect.value;
  if (!pointId) {
    setTaskStatus("请选择点位", true);
    return;
  }
  currentTourTask.steps = normalizeTaskSteps();
  currentTourTask.steps.push({point_id: pointId, enabled: true});
  renderTaskEditor();
}

function moveTaskStep(index, delta) {
  currentTourTask.steps = normalizeTaskSteps();
  const nextIndex = index + delta;
  if (nextIndex < 0 || nextIndex >= currentTourTask.steps.length) return;
  const [step] = currentTourTask.steps.splice(index, 1);
  currentTourTask.steps.splice(nextIndex, 0, step);
  renderTaskEditor();
}

function removeTaskStep(index) {
  currentTourTask.steps = normalizeTaskSteps();
  currentTourTask.steps.splice(index, 1);
  renderTaskEditor();
}

async function saveTask() {
  if (!currentMap) {
    setTaskStatus("请先加载地图", true);
    return;
  }
  const payload = {
    map_name: currentMap.map_name,
    map_id: currentMap.map_id,
    name: taskNameInput.value.trim() || "默认讲解任务",
    enabled: Boolean(taskEnabledInput.checked),
    steps: normalizeTaskSteps()
  };
  const resp = await fetch("/api/tour-task", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload)
  });
  const result = await resp.json();
  if (!resp.ok) {
    setTaskStatus(result.error || "保存任务失败", true);
    return;
  }
  currentTourTask = result;
  renderTaskEditor();
  setTaskStatus(`任务已保存，共 ${currentTourTask.steps.length} 个点位`);
}

function editPoint(point) {
  editingPointId = point.id;
  const pos = point.pose.position || {};
  const nav = point.navigation || {};
  const ui = point.ui || {};
  const yaw = quaternionToYaw(point);
  pointNameInput.value = point.name || "";
  aliasesInput.value = (point.aliases || []).join(",");
  pointXInput.value = Number(pos.x || 0).toFixed(3);
  pointYInput.value = Number(pos.y || 0).toFixed(3);
  pointZInput.value = Number(pos.z || 0).toFixed(2);
  setCurrentYaw(yaw);
  radiusInput.value = String(nav.target_pose_radius ?? 0.15);
  maxSpeedInput.value = String(nav.max_forward_speed ?? 0.3);
  pncModeInput.value = String(nav.pnc_mode ?? 0);
  taskTypeInput.value = String(nav.task_type ?? 2);
  isStartPointInput.checked = Boolean(point.is_start);
  const actions = getArrivalActions(point);
  const ttsAction = actions.find((action) => action && action.type === "tts");
  arrivalTtsInput.value = ttsAction && ttsAction.text ? ttsAction.text : "";
  const motionAction = actions.find((action) => action && action.type === "motion");
  arrivalMotionInput.value = motionAction && motionAction.motion ? motionAction.motion : "";
  syncArrivalMotionArea(motionAction && motionAction.area ? motionAction.area : "right_hand");
  const emojiAction = actions.find((action) => action && action.type === "emoji");
  arrivalEmojiInput.value = emojiAction && emojiAction.emotion ? emojiAction.emotion : "";
  arrivalEmojiModeInput.value = emojiAction && emojiAction.mode ? emojiAction.mode : "once";
  const pixelU = Number(ui.pixel_u);
  const pixelV = Number(ui.pixel_v);
  targetPixel = Number.isFinite(pixelU) && Number.isFinite(pixelV)
    ? {u: pixelU, v: pixelV}
    : worldToPixel({x: Number(pos.x || 0), y: Number(pos.y || 0)});
  const headingU = Number(ui.heading_pixel_u);
  const headingV = Number(ui.heading_pixel_v);
  headingPixel = Number.isFinite(headingU) && Number.isFinite(headingV)
    ? {u: headingU, v: headingV}
    : {u: targetPixel.u + 28 * Math.cos(yaw), v: targetPixel.v - 28 * Math.sin(yaw)};
  awaitingHeading = false;
  drawMap();
  setStatus(`正在编辑点位：${point.name}`);
}

canvas.addEventListener("click", (event) => {
  if (!currentMap) return;
  const rect = canvas.getBoundingClientRect();
  const scaleX = canvas.width / rect.width;
  const scaleY = canvas.height / rect.height;
  const u = (event.clientX - rect.left) * scaleX;
  const v = (event.clientY - rect.top) * scaleY;
  if (!targetPixel || !awaitingHeading) {
    targetPixel = {u, v};
    headingPixel = null;
    awaitingHeading = true;
    const world = pixelToWorld(u, v);
    pointXInput.value = world.x.toFixed(3);
    pointYInput.value = world.y.toFixed(3);
    if (!pointZInput.value.trim()) pointZInput.value = "0.0";
    setCurrentYaw(0);
    drawMap();
    setStatus(`已选择坐标 x=${world.x.toFixed(3)}, y=${world.y.toFixed(3)}；请再点击设置朝向`);
    return;
  }
  setHeadingPixel(u, v, "已设置朝向");
});

canvas.addEventListener("contextmenu", (event) => {
  event.preventDefault();
  if (!currentMap || !targetPixel) {
    setStatus("请先左键选择点位位置，再右键设置朝向", true);
    return;
  }
  const rect = canvas.getBoundingClientRect();
  const scaleX = canvas.width / rect.width;
  const scaleY = canvas.height / rect.height;
  setHeadingPixel((event.clientX - rect.left) * scaleX, (event.clientY - rect.top) * scaleY, "已通过右键设置朝向");
});

function setHeadingPixel(u, v, prefix) {
  headingPixel = {u, v};
  const dx = u - targetPixel.u;
  const dy = targetPixel.v - v;
  setCurrentYaw(Math.abs(dx) < 1e-6 && Math.abs(dy) < 1e-6 ? 0 : Math.atan2(dy, dx));
  awaitingHeading = false;
  drawMap();
  setStatus(`${prefix}：yaw=${pointYawInput.value}°`);
}

function fillRelocFromPoint() {
  if (!currentMap || !targetPixel) {
    setRelocStatus("请先加载地图并选择一个位置", true);
    return;
  }
  relocMapIdInput.value = String(currentMap.map_id);
  relocXInput.value = targetPixel.u.toFixed(3);
  relocYInput.value = targetPixel.v.toFixed(3);
  relocZInput.value = pointZInput.value || "0.0";
  relocQxInput.value = "0.0";
  relocQyInput.value = "0.0";
  relocQzInput.value = Math.sin(currentYaw / 2).toFixed(12);
  relocQwInput.value = Math.cos(currentYaw / 2).toFixed(12);
  setRelocStatus("已填入当前选点的像素坐标");
}

async function savePoint() {
  if (!currentMap || !targetPixel || !pointXInput.value || !pointYInput.value) {
    setStatus("请先加载地图并点击目标位置", true);
    return;
  }
  const name = pointNameInput.value.trim();
  if (!name) {
    setStatus("点位名不能为空", true);
    return;
  }
  const tts = arrivalTtsInput.value.trim();
  const onArrival = [];
  if (arrivalMotionInput.value) {
    onArrival.push({
      type: "motion",
      motion: arrivalMotionInput.value,
      area: arrivalMotionAreaInput.value || "right_hand",
      interrupt: ["empty_motion", "random_speech"].includes(arrivalMotionInput.value)
    });
  }
  if (arrivalEmojiInput.value) {
    onArrival.push({
      type: "emoji",
      emotion: arrivalEmojiInput.value,
      mode: arrivalEmojiModeInput.value || "once"
    });
  }
  if (tts) {
    onArrival.push({type: "tts", text: tts});
  }
  const payload = {
    name,
    is_start: Boolean(isStartPointInput.checked),
    aliases: aliasesInput.value.split(",").map(s => s.trim()).filter(Boolean),
    map_name: currentMap.map_name,
    map_id: currentMap.map_id,
    pose: {
      position: {x: Number(pointXInput.value), y: Number(pointYInput.value), z: Number(pointZInput.value || "0")},
      orientation: {x: 0, y: 0, z: Math.sin(currentYaw / 2), w: Math.cos(currentYaw / 2)}
    },
    navigation: {
      target_pose_radius: Number(radiusInput.value || "0.15"),
      max_forward_speed: Number(maxSpeedInput.value || "0.3"),
      pnc_mode: Number(pncModeInput.value || "0"),
      task_type: Number(taskTypeInput.value || "2")
    },
    ui: {
      pixel_u: targetPixel.u,
      pixel_v: targetPixel.v,
      heading_pixel_u: headingPixel ? headingPixel.u : null,
      heading_pixel_v: headingPixel ? headingPixel.v : null
    },
    voice: {enabled: true, display_name: name},
    business: {on_arrival: onArrival}
  };
  if (editingPointId) payload.id = editingPointId;
  const resp = await fetch("/api/points", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload)
  });
  const result = await resp.json();
  if (!resp.ok) {
    setStatus(result.error || "保存失败", true);
    return;
  }
  pointNameInput.value = "";
  aliasesInput.value = "";
  resetPlacementState();
  resetPointFields();
  await loadMap(currentMap.map_name);
  setStatus(`已保存点位：${result.name}`);
}

async function loadConfigInfo() {
  const resp = await fetch("/api/config");
  const payload = await resp.json();
  if (resp.ok) configPathEl.textContent = `配置：${payload.config_path}\n点位目录：${payload.points_dir}`;
}

async function loadSettings() {
  const resp = await fetch("/api/settings");
  const payload = await resp.json();
  if (resp.ok) {
    navModeInput.value = payload.nav_mode;
    const safety = payload.path_safety || {};
    robotRadiusInput.value = safety.robot_radius_m ?? "";
    extraClearanceInput.value = safety.extra_clearance_m ?? 0.10;
    occupiedThresholdInput.value = safety.occupied_threshold ?? 205;
  }
}

async function saveSettings() {
  const resp = await fetch("/api/settings", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      nav_mode: navModeInput.value,
      path_safety: {
        robot_radius_m: robotRadiusInput.value === "" ? null : Number(robotRadiusInput.value),
        extra_clearance_m: Number(extraClearanceInput.value),
        occupied_threshold: Number(occupiedThresholdInput.value)
      }
    })
  });
  const payload = await resp.json();
  if (!resp.ok) {
    setBridgeStatus(payload.error || "保存模式失败", true);
    return;
  }
  navModeInput.value = payload.nav_mode;
  setBridgeStatus(`导航模式和全局路径安全参数已保存：${payload.nav_mode}`);
}

async function refreshBridgeStatus() {
  const resp = await fetch("/api/bridge/status");
  const payload = await resp.json();
  if (!resp.ok) {
    setBridgeStatus(payload.error || "读取桥接状态失败", true);
    return;
  }
  const bridge = payload.bridge || {};
  const remote = bridge.nav_mode ? `\n运行模式：${bridge.nav_mode}` : "";
  setBridgeStatus(`桥接：${payload.running ? "running" : "stopped"}${payload.pid ? " pid=" + payload.pid : ""}${remote}\nHTTP：${payload.base_url}\n日志：${payload.log_file}`);
}

async function startBridge() {
  await saveSettings();
  const resp = await fetch("/api/bridge/start", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({active_map_name: currentMap ? currentMap.map_name : ""})
  });
  const payload = await resp.json();
  if (!resp.ok) {
    setBridgeStatus(payload.error || "启动失败", true);
    return;
  }
  setBridgeStatus(`桥接已启动：pid=${payload.pid}\nHTTP：${payload.base_url}\n日志：${payload.log_file}`);
}

async function stopBridge() {
  const resp = await fetch("/api/bridge/stop", {method: "POST"});
  const payload = await resp.json();
  if (!resp.ok) {
    setBridgeStatus(payload.error || "停止失败", true);
    return;
  }
  setBridgeStatus(`桥接：${payload.running ? "running" : "stopped"}`);
}

async function refreshRelocStatus() {
  const resp = await fetch("/api/relocalization/status");
  const payload = await resp.json();
  if (!resp.ok) {
    setRelocStatus(payload.error || "读取重定位状态失败", true);
    return;
  }
  const result = payload.result || {};
  const label = result.status ? `${result.status} - ${result.message || ""}` : (payload.running ? "running" : "stopped");
  setRelocStatus(`重定位：${label}${payload.pid ? " pid=" + payload.pid : ""}\n日志：${payload.log_file}`);
}

async function startRelocalization() {
  const mapId = Number(relocMapIdInput.value || "0");
  if (!mapId || !relocXInput.value || !relocYInput.value) {
    setRelocStatus("请填写 map_id、X、Y", true);
    return;
  }
  const relocX = Number(relocXInput.value);
  const relocY = Number(relocYInput.value);
  if (!Number.isFinite(relocX) || !Number.isFinite(relocY)) {
    setRelocStatus("重定位像素坐标必须是数字", true);
    return;
  }
  if (
    currentMap &&
    currentMap.map_id === mapId &&
    (relocX < 0 || relocY < 0 || relocX > currentMap.width || relocY > currentMap.height)
  ) {
    setRelocStatus(`重定位像素坐标超出地图范围：x=${relocX}, y=${relocY}, map=${currentMap.width}x${currentMap.height}`, true);
    return;
  }
  const resp = await fetch("/api/relocalization/start", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      map_id: mapId,
      x: relocX,
      y: relocY,
      map_root: mapRootInput.value,
      z: Number(relocZInput.value || "0"),
      qx: Number(relocQxInput.value || "0"),
      qy: Number(relocQyInput.value || "0"),
      qz: Number(relocQzInput.value || "0"),
      qw: Number(relocQwInput.value || "1"),
      timeout_sec: Number(relocTimeoutInput.value || "30")
    })
  });
  const payload = await resp.json();
  if (!resp.ok) {
    setRelocStatus(payload.error || "重定位启动失败", true);
    return;
  }
  const result = payload.result || {};
  setRelocStatus(`重定位已启动：${result.message || "等待结果"}${payload.pid ? " pid=" + payload.pid : ""}\n日志：${payload.log_file}`);
}

document.getElementById("discoverBtn").onclick = discoverMaps;
document.getElementById("savePointBtn").onclick = savePoint;
document.getElementById("resetYawBtn").onclick = () => {
  headingPixel = null;
  awaitingHeading = Boolean(targetPixel);
  setCurrentYaw(0);
  drawMap();
  setStatus("已将朝向重置为 0°");
};
document.getElementById("saveSettingsBtn").onclick = saveSettings;
document.getElementById("startBridgeBtn").onclick = startBridge;
document.getElementById("stopBridgeBtn").onclick = stopBridge;
document.getElementById("refreshBridgeBtn").onclick = refreshBridgeStatus;
document.getElementById("fillRelocFromPointBtn").onclick = fillRelocFromPoint;
document.getElementById("startRelocBtn").onclick = startRelocalization;
document.getElementById("refreshRelocBtn").onclick = refreshRelocStatus;
document.getElementById("addTaskStepBtn").onclick = addTaskStep;
document.getElementById("refreshLinkcraftBtn").onclick = () => loadLinkcraftResources(true);
document.getElementById("saveTaskBtn").onclick = saveTask;
loadConfigInfo();
loadSettings();
refreshBridgeStatus();
refreshRelocStatus();
discoverMaps();
</script>
</body>
</html>
"""


class V3LabelApp:
    def __init__(
        self,
        config_store: UnifiedConfigStore,
        point_store: MapPointStore,
        bridge_manager: "BridgeServiceManager",
        relocalization_manager: "RelocalizationTaskManager",
    ):
        self.config_store = config_store
        self.point_store = point_store
        self.bridge_manager = bridge_manager
        self.relocalization_manager = relocalization_manager

    def get_config_payload(self) -> dict[str, object]:
        payload = self.config_store.load().to_dict()
        payload["config_path"] = str(self.config_store.file_path)
        payload["points_dir"] = str(self.point_store.maps_dir)
        return payload

    def get_settings(self) -> dict[str, object]:
        config = self.config_store.load()
        bridge_status = self.bridge_manager.status()
        bridge = bridge_status.get("bridge")
        bridge = bridge if isinstance(bridge, dict) else {}
        applied_path_safety = bridge.get("path_safety")
        return {
            **config.app.__dict__,
            "path_safety": config.navigation.path_safety.__dict__,
            "path_safety_applied": bool(
                bridge.get("ok") is True
                and path_safety_matches(
                    config.navigation.path_safety.__dict__, applied_path_safety
                )
            ),
            "applied_path_safety": applied_path_safety,
            "bridge_running": bridge.get("ok") is True,
        }

    def save_settings(self, payload: dict[str, object]) -> dict[str, object]:
        config = self.config_store.load()
        merged = config.app.__dict__.copy()
        merged.update({key: value for key, value in payload.items() if key != "path_safety"})
        config.app = AppSettings.from_dict(merged)
        path_safety = payload.get("path_safety")
        if path_safety is not None:
            if not isinstance(path_safety, dict):
                raise ValueError("path_safety must be an object")
            safety_payload = config.navigation.path_safety.__dict__.copy()
            safety_payload.update(path_safety)
            config.navigation.path_safety = config.navigation.path_safety.from_dict(safety_payload)
        self.config_store.save(config)
        bridge_update = self.bridge_manager.set_nav_mode(
            config.app.nav_mode,
            path_safety=config.navigation.path_safety.__dict__,
        )
        return {
            **config.app.__dict__,
            "path_safety": config.navigation.path_safety.__dict__,
            "path_safety_saved": True,
            "path_safety_applied": bool(bridge_update.get("applied")),
            "applied_path_safety": bridge_update.get("path_safety"),
            "bridge_running": bool(bridge_update.get("running")),
            "path_safety_apply_error": bridge_update.get("error", ""),
        }

    def get_bridge_status(self) -> dict[str, object]:
        return self.bridge_manager.status()

    def start_bridge(self, payload: dict[str, object]) -> dict[str, object]:
        active_map_name = str(payload.get("active_map_name", "")).strip() or None
        config = self.config_store.load()
        return self.bridge_manager.start(config=config, active_map_name=active_map_name)

    def stop_bridge(self) -> dict[str, object]:
        return self.bridge_manager.stop()

    def get_relocalization_status(self) -> dict[str, object]:
        return self.relocalization_manager.status()

    def get_linkcraft_resources(self) -> dict[str, object]:
        console_url = os.environ.get(
            "LINKCRAFT_RESOURCE_API_URL",
            "http://127.0.0.1:3000/api/robots/default/linkcraft/resources",
        ).strip()
        try:
            with urllib.request.urlopen(console_url, timeout=5.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if isinstance(payload, dict):
                return payload
        except (OSError, ValueError, urllib.error.URLError):
            pass
        path = Path(os.environ.get(
            "LINKCRAFT_RESOURCE_CONFIG_PATH",
            str(DEFAULT_RESOURCE_CONFIG_PATH),
        )).expanduser()
        resources = discover_linkcraft_resources(path)
        return {"ok": True, "resource_count": len(resources), "resources": resources}

    def start_relocalization(self, payload: dict[str, object]) -> dict[str, object]:
        return self.relocalization_manager.start(payload)

    def discover_maps(self, map_root: str) -> list[dict[str, object]]:
        root = resolve_map_root(map_root)
        maps = []
        for child in sorted(root.iterdir(), key=lambda entry: entry.name):
            if not child.is_dir() or not child.name.isdigit():
                continue
            map_image = child / "occupancy_map.png"
            grid_info = child / "grid_map_info.txt"
            metadata = child / "lingxi_map_import.json"
            if not map_image.is_file():
                continue
            if grid_info.is_file():
                maps.append(build_map_payload(child.name, int(child.name), map_image))
            else:
                if not metadata.is_file():
                    continue
                imported = json.loads(metadata.read_text(encoding="utf-8"))
                if imported.get("map_id") != int(child.name) or imported.get("map_name") != child.name:
                    continue
                info = imported.get("map_info")
                if not isinstance(info, dict):
                    continue
                maps.append({
                    "map_name": child.name,
                    "map_id": int(child.name),
                    "map_path": str(map_image),
                    "grid_map_info_path": str(grid_info),
                    **info,
                })
        return maps

    def get_map_payload(self, map_root: str, map_name: str) -> dict[str, object]:
        map_payload = self._get_map(map_root, map_name)
        config = self.config_store.load()
        points = [
            point_to_payload(point)
            for point in self.point_store.load_points(
                str(map_payload["map_name"]),
                defaults=config.navigation,
            )
        ]
        tour_tasks = self.point_store.load_tour_tasks(str(map_payload["map_name"]))
        return {
            "map": map_payload,
            "points": points,
            "tour_task": tour_tasks["active_task"],
            "tour_tasks": tour_tasks["tasks"],
            "active_tour_task_id": tour_tasks["active_tour_task_id"],
        }

    def get_map_image(self, map_root: str, map_name: str) -> tuple[bytes, str]:
        map_payload = self._get_map(map_root, map_name)
        image_path = Path(str(map_payload["map_path"]))
        return image_path.read_bytes(), mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"

    def save_point(self, payload: dict[str, object]) -> dict[str, object]:
        config = self.config_store.load()
        point = NavPoint.from_dict(payload, defaults=config.navigation)
        self.point_store.upsert_point(point, defaults=config.navigation)
        config = upsert_map(config, point)
        self.config_store.save(config)
        self.bridge_manager.set_active_map(point.map_name)
        self.bridge_manager.reload_catalog()
        return point_to_payload(point)

    def delete_point(self, point_id: str) -> bool:
        config = self.config_store.load()
        deleted = self.point_store.delete_point_by_id(point_id, defaults=config.navigation)
        if deleted:
            self.bridge_manager.reload_catalog()
        return deleted

    def save_tour_task(self, payload: dict[str, object]) -> dict[str, object]:
        map_name = str(payload.get("map_name", "")).strip()
        map_id = int(payload.get("map_id", 0) or 0)
        if not map_name:
            raise ValueError("map_name is required")
        if map_id <= 0:
            raise ValueError("map_id is required")
        known_point_ids = {
            point.id
            for point in self.point_store.load_points(
                map_name,
                defaults=self.config_store.load().navigation,
            )
        }
        steps = payload.get("steps", [])
        if not isinstance(steps, list):
            raise ValueError("steps must be a list")
        for item in steps:
            if isinstance(item, dict):
                point_id = str(item.get("point_id", item.get("id", ""))).strip()
            else:
                point_id = str(item).strip()
            if point_id and point_id not in known_point_ids:
                raise ValueError(f"unknown point_id in tour task: {point_id}")
        task = self.point_store.save_tour_task(
            map_name,
            map_id,
            {
                "id": payload.get("id", ""),
                "name": payload.get("name", "默认讲解任务"),
                "enabled": payload.get("enabled", True),
                "steps": steps,
            },
        )
        self.bridge_manager.reload_catalog()
        return task

    def _get_map(self, map_root: str, map_name: str) -> dict[str, object]:
        map_dir = resolve_map_root(map_root) / map_name
        if not map_dir.is_dir() or not map_name.isdigit():
            raise FileNotFoundError(f"Map not found: {map_name}")
        image = map_dir / "occupancy_map.png"
        grid_info = map_dir / "grid_map_info.txt"
        metadata = map_dir / "lingxi_map_import.json"
        if image.is_file() and grid_info.is_file():
            return build_map_payload(map_name, int(map_name), image)
        if metadata.is_file() and image.is_file():
            imported = json.loads(metadata.read_text(encoding="utf-8"))
            if imported.get("map_id") == int(map_name) and imported.get("map_name") == map_name:
                info = imported.get("map_info")
                if isinstance(info, dict):
                    return {
                        "map_name": map_name,
                        "map_id": int(map_name),
                        "map_path": str(image),
                        "grid_map_info_path": str(grid_info),
                        **info,
                    }
        raise FileNotFoundError(f"Map metadata is incomplete: {map_name}")


class V3LabelRequestHandler(BaseHTTPRequestHandler):
    app: V3LabelApp
    default_map_root: str

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                self._send_html(HTML_PAGE.replace("__DEFAULT_MAP_ROOT__", html.escape(self.default_map_root, quote=True)))
                return
            if parsed.path == "/api/config":
                self._send_json(self.app.get_config_payload())
                return
            if parsed.path == "/api/settings":
                self._send_json(self.app.get_settings())
                return
            if parsed.path == "/api/bridge/status":
                self._send_json(self.app.get_bridge_status())
                return
            if parsed.path == "/api/relocalization/status":
                self._send_json(self.app.get_relocalization_status())
                return
            if parsed.path == "/api/linkcraft_resources":
                self._send_json(self.app.get_linkcraft_resources())
                return
            if parsed.path == "/api/discover_maps":
                self._send_json({"maps": self.app.discover_maps(self._require_query_param(parsed, "map_root"))})
                return
            if parsed.path == "/api/map":
                self._send_json(
                    self.app.get_map_payload(
                        self._require_query_param(parsed, "map_root"),
                        self._require_query_param(parsed, "map_name"),
                    )
                )
                return
            if parsed.path == "/api/map_image":
                data, content_type = self.app.get_map_image(
                    self._require_query_param(parsed, "map_root"),
                    self._require_query_param(parsed, "map_name"),
                )
                self._send_bytes(data, content_type)
                return
            self._send_error_json(HTTPStatus.NOT_FOUND, "Unknown endpoint")
        except Exception as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/points":
                self._send_json(self.app.save_point(self._read_json_body()), status=HTTPStatus.CREATED)
                return
            if parsed.path == "/api/settings":
                self._send_json(self.app.save_settings(self._read_json_body()))
                return
            if parsed.path == "/api/tour-task":
                self._send_json(self.app.save_tour_task(self._read_json_body()))
                return
            if parsed.path == "/api/bridge/start":
                self._send_json(self.app.start_bridge(self._read_json_body()))
                return
            if parsed.path == "/api/bridge/stop":
                self._send_json(self.app.stop_bridge())
                return
            if parsed.path == "/api/relocalization/start":
                self._send_json(self.app.start_relocalization(self._read_json_body()))
                return
            self._send_error_json(HTTPStatus.NOT_FOUND, "Unknown endpoint")
        except Exception as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/points/"):
            self._send_error_json(HTTPStatus.NOT_FOUND, "Unknown endpoint")
            return
        try:
            point_id = unquote(parsed.path.removeprefix("/api/points/"))
            if self.app.delete_point(point_id):
                self._send_empty(HTTPStatus.NO_CONTENT)
            else:
                self._send_error_json(HTTPStatus.NOT_FOUND, "Point not found")
        except Exception as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))

    def log_message(self, format: str, *args) -> None:
        return

    def _read_json_body(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length)
        if not body:
            return {}
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    def _require_query_param(self, parsed, name: str) -> str:
        value = parse_qs(parsed.query).get(name, [""])[0].strip()
        if not value:
            raise ValueError(f"Missing query parameter: {name}")
        return value

    def _send_html(self, body: str) -> None:
        self._send_bytes(body.encode("utf-8"), "text/html; charset=utf-8")

    def _send_json(self, payload: dict[str, object], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: HTTPStatus, message: str) -> None:
        self._send_json({"error": message}, status=status)

    def _send_empty(self, status: HTTPStatus) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_bytes(self, body: bytes, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class BridgeServiceManager:
    """Starts and stops the v3 Lingxi HTTP bridge without using voice_agent_v2."""

    def __init__(self, *, config_path: Path, app_home: Path, bridge_host: str, bridge_port: int):
        self.config_path = config_path.expanduser().resolve()
        self.app_home = app_home.expanduser().resolve()
        self.bridge_host = bridge_host
        self.bridge_port = int(bridge_port)
        client_host = "127.0.0.1" if bridge_host in {"0.0.0.0", "::"} else bridge_host
        self.base_url = f"http://{client_host}:{bridge_port}"
        self.pid_dir = self.app_home / "pids"
        self.log_dir = self.app_home / "logs"
        self.pid_file = self.pid_dir / "lingxi_nav_bridge.pid"
        self.log_file = self.log_dir / "lingxi_nav_bridge.log"

    def status(self) -> dict[str, object]:
        pid = self._read_pid()
        running = self._is_running(pid)
        if pid is not None and not running:
            self._remove_pid_file()
            pid = None
        return {
            "running": running,
            "pid": pid,
            "pid_file": str(self.pid_file),
            "log_file": str(self.log_file),
            "config_path": str(self.config_path),
            "base_url": self.base_url,
            "bridge": self._get_remote_status(),
        }

    def start(self, *, config: UnifiedNavConfig, active_map_name: str | None) -> dict[str, object]:
        self.stop()
        self.pid_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            "-m",
            "robot_local_navigation_v3.lingxi_nav_bridge",
            "--config-json",
            str(self.config_path),
            "--nav-mode",
            config.app.nav_mode,
            "--host",
            self.bridge_host,
            "--port",
            str(self.bridge_port),
        ]
        if active_map_name:
            command.extend(["--active-map-name", active_map_name])

        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=os.environ.copy(),
            text=True,
            bufsize=1,
            start_new_session=True,
            close_fds=True,
        )
        threading.Thread(
            target=self._forward_bridge_logs,
            args=(process,),
            daemon=True,
        ).start()
        self.pid_file.write_text(str(process.pid), encoding="utf-8")
        return self.status()

    def stop(self) -> dict[str, object]:
        pid = self._read_pid()
        if pid is not None and self._is_running(pid):
            os.kill(pid, signal.SIGTERM)
        self._stop_matching_processes()
        self._remove_pid_file()
        return self.status()

    def set_nav_mode(
        self,
        mode: str,
        *,
        path_safety: dict[str, object] | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {"nav_mode": mode}
        if path_safety is not None:
            payload["path_safety"] = path_safety
        remote_status = self._get_remote_status()
        running = remote_status.get("ok") is True
        if not running:
            return {"running": False, "applied": False, "error": "导航桥接未在线"}
        try:
            result = self._post_json("/nav/mode", payload)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            return {"running": False, "applied": False, "error": str(exc)}
        if result.get("ok") is not True:
            return {
                "running": True,
                "applied": False,
                "path_safety": None,
                "error": str(result.get("message", "导航桥接拒绝应用安全参数")),
            }
        runtime_status = self._get_remote_status()
        applied_path_safety = runtime_status.get("path_safety")
        applied = (
            runtime_status.get("ok") is True
            and path_safety_matches(path_safety or {}, applied_path_safety)
        )
        return {
            "running": True,
            "applied": applied,
            "path_safety": applied_path_safety,
            "error": "" if applied else "桥接未回读到与网页保存值一致的全局路径安全参数",
        }

    def set_active_map(self, map_name: str) -> None:
        self._post_json("/nav/map", {"active_map_name": map_name})

    def reload_catalog(self) -> None:
        self._post_json("/nav/reload", {})

    def _forward_bridge_logs(self, process: subprocess.Popen) -> None:
        if process.stdout is None:
            return
        self.log_dir.mkdir(parents=True, exist_ok=True)
        with self.log_file.open("a", encoding="utf-8") as log_handle:
            for line in process.stdout:
                log_handle.write(line)
                log_handle.flush()
                print(f"[lingxi_nav_bridge] {line}", end="", flush=True)

    def _get_remote_status(self) -> dict[str, object]:
        try:
            with urllib.request.urlopen(f"{self.base_url}/nav/status", timeout=0.5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _post_json(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=0.5) as response:
            result = json.loads(response.read().decode("utf-8"))
        return result if isinstance(result, dict) else {}

    def _read_pid(self) -> int | None:
        if not self.pid_file.exists():
            return None
        try:
            return int(self.pid_file.read_text(encoding="utf-8").strip())
        except ValueError:
            self._remove_pid_file()
            return None

    def _is_running(self, pid: int | None) -> bool:
        if pid is None:
            return False
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            parts = stat.split()
            if len(parts) >= 3 and parts[2] == "Z":
                return False
        except OSError:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def _remove_pid_file(self) -> None:
        try:
            self.pid_file.unlink()
        except FileNotFoundError:
            return

    def _stop_matching_processes(self) -> None:
        try:
            output = subprocess.check_output(["ps", "-ef"], text=True)
        except Exception:
            return
        current_pid = os.getpid()
        for line in output.splitlines():
            if "robot_local_navigation_v3.lingxi_nav_bridge" not in line:
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            try:
                pid = int(parts[1])
            except ValueError:
                continue
            if pid == current_pid:
                continue
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                continue


class RelocalizationTaskManager:
    """Runs relocalization as a short-lived ROS process."""

    def __init__(self, *, app_home: Path):
        self.app_home = app_home.expanduser().resolve()
        self.pid_dir = self.app_home / "pids"
        self.log_dir = self.app_home / "logs"
        self.pid_file = self.pid_dir / "relocalize_once.pid"
        self.log_file = self.log_dir / "relocalize_once.log"
        self.result_file = self.app_home / "relocalization_result.json"

    def status(self) -> dict[str, object]:
        pid = self._read_pid()
        running = self._is_running(pid)
        if pid is not None and not running:
            self._remove_pid_file()
            pid = None
        return {
            "running": running,
            "pid": pid,
            "pid_file": str(self.pid_file),
            "log_file": str(self.log_file),
            "result_file": str(self.result_file),
            "result": self._read_result(),
        }

    def start(self, payload: dict[str, object]) -> dict[str, object]:
        current = self.status()
        if current["running"]:
            return current

        map_id = int(payload.get("map_id", 0))
        if map_id <= 0:
            raise ValueError("map_id is required")
        x = float(payload["x"])
        y = float(payload["y"])
        map_root = str(payload.get("map_root", "")).strip()
        z = float(payload.get("z", 0.0))
        qx = float(payload.get("qx", 0.0))
        qy = float(payload.get("qy", 0.0))
        qz = float(payload.get("qz", 0.0))
        qw = float(payload.get("qw", 1.0))
        timeout_sec = float(payload.get("timeout_sec", 30.0))
        verification_args: list[str] = []
        result_payload: dict[str, object] = {
            "status": "starting",
            "message": "正在启动重定位",
            "map_id": map_id,
            "pixel_x": x,
            "pixel_y": y,
        }
        if map_root:
            map_dir = resolve_map_root(map_root) / str(map_id)
            grid_info = parse_grid_map_info(map_dir / "grid_map_info.txt")
            width = float(grid_info["width"])
            height = float(grid_info["height"])
            if x < 0 or y < 0 or x > width or y > height:
                raise ValueError(f"relocalization pixel coordinate out of map bounds: x={x}, y={y}, map={width:g}x{height:g}")
            resolution_pixels_per_meter = float(grid_info["resolution_pixels_per_meter"])
            origin_u = float(grid_info["origin_u"])
            origin_v = float(grid_info["origin_v"])
            expected_world_x = (x - origin_u) / resolution_pixels_per_meter
            expected_world_y = -(y - origin_v) / resolution_pixels_per_meter
            verify_tolerance_m = float(payload.get("verify_tolerance_m", 2.0))
            verification_args = [
                "--expected-world-x",
                str(expected_world_x),
                "--expected-world-y",
                str(expected_world_y),
                "--verify-tolerance-m",
                str(verify_tolerance_m),
            ]
            result_payload.update(
                {
                    "message": "正在启动重定位（像素坐标，已启用定位回验）",
                    "expected_world_x": expected_world_x,
                    "expected_world_y": expected_world_y,
                    "verify_tolerance_m": verify_tolerance_m,
                    "grid_map_info_path": str(map_dir / "grid_map_info.txt"),
                }
            )
        else:
            result_payload["message"] = "正在启动重定位（未提供 map_root，定位回验未启用）"

        self.pid_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._write_result(result_payload)
        command = [
            sys.executable,
            "-m",
            "robot_local_navigation_v3.relocalize_once",
            "--map-id",
            str(map_id),
            "--x",
            str(x),
            "--y",
            str(y),
            "--z",
            str(z),
            "--qx",
            str(qx),
            "--qy",
            str(qy),
            "--qz",
            str(qz),
            "--qw",
            str(qw),
            "--timeout-sec",
            str(timeout_sec),
            "--result-json",
            str(self.result_file),
        ]
        command.extend(verification_args)
        with self.log_file.open("ab") as log_handle:
            process = subprocess.Popen(
                command,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=os.environ.copy(),
                start_new_session=True,
                close_fds=True,
            )
        self.pid_file.write_text(str(process.pid), encoding="utf-8")
        return self.status()

    def _read_result(self) -> dict[str, object]:
        if not self.result_file.exists():
            return {}
        try:
            payload = json.loads(self.result_file.read_text(encoding="utf-8"))
        except Exception:
            return {"status": "unknown", "message": "无法读取重定位结果"}
        return payload if isinstance(payload, dict) else {}

    def _write_result(self, payload: dict[str, object]) -> None:
        payload = dict(payload)
        payload["updated_at"] = time.time()
        self.result_file.parent.mkdir(parents=True, exist_ok=True)
        self.result_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _read_pid(self) -> int | None:
        if not self.pid_file.exists():
            return None
        try:
            return int(self.pid_file.read_text(encoding="utf-8").strip())
        except ValueError:
            self._remove_pid_file()
            return None

    def _is_running(self, pid: int | None) -> bool:
        if pid is None:
            return False
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            parts = stat.split()
            if len(parts) >= 3 and parts[2] == "Z":
                return False
        except OSError:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def _remove_pid_file(self) -> None:
        try:
            self.pid_file.unlink()
        except FileNotFoundError:
            return


def parse_grid_map_info(path: Path) -> dict[str, object]:
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(lines) < 3:
        raise ValueError(f"Invalid grid_map_info.txt: {path}")
    resolution_pixels_per_meter = float(lines[0].split()[0])
    origin_u, origin_v = [float(value) for value in lines[1].split()[:2]]
    width, height = [int(float(value)) for value in lines[2].split()[:2]]
    return {
        "resolution_pixels_per_meter": resolution_pixels_per_meter,
        "origin_u": origin_u,
        "origin_v": origin_v,
        "width": width,
        "height": height,
    }


def path_safety_matches(expected: object, actual: object) -> bool:
    if not isinstance(expected, dict) or not isinstance(actual, dict):
        return False
    numeric_fields = (
        "robot_radius_m",
        "extra_clearance_m",
        "max_odom_age_sec",
        "max_start_drift_m",
    )
    integer_fields = ("occupied_threshold", "max_reference_points")
    for name in numeric_fields:
        left, right = expected.get(name), actual.get(name)
        if left is None or right is None:
            if left is not right:
                return False
            continue
        try:
            if not math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-9):
                return False
        except (TypeError, ValueError):
            return False
    for name in integer_fields:
        try:
            if int(expected.get(name)) != int(actual.get(name)):
                return False
        except (TypeError, ValueError):
            return False
    return True


def build_map_payload(map_name: str, map_id: int, map_path: Path) -> dict[str, object]:
    if not map_path.exists():
        raise FileNotFoundError(f"Map image not found: {map_path}")
    grid_info_path = map_path.parent / "grid_map_info.txt"
    grid_info = parse_grid_map_info(grid_info_path)
    return {
        "map_name": map_name,
        "map_id": map_id,
        "map_path": str(map_path),
        "grid_map_info_path": str(grid_info_path),
        **grid_info,
    }


def resolve_map_root(map_root: str) -> Path:
    root = Path(map_root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"Map root does not exist: {root}")
    return root


def upsert_map(config: UnifiedNavConfig, point: NavPoint) -> UnifiedNavConfig:
    for current in config.maps:
        if current.map_name == point.map_name and current.map_id == point.map_id:
            return config
    config.maps.append(MapConfig(map_name=point.map_name, map_id=point.map_id))
    return config


def point_to_payload(point: NavPoint) -> dict[str, object]:
    return {
        "id": point.id,
        "name": point.name,
        "is_start": point.is_start,
        "aliases": point.aliases,
        "map_name": point.map_name,
        "map_id": point.map_id,
        "pose": {
            "position": {
                "x": point.pose.position.x,
                "y": point.pose.position.y,
                "z": point.pose.position.z,
            },
            "orientation": {
                "x": point.pose.orientation.x,
                "y": point.pose.orientation.y,
                "z": point.pose.orientation.z,
                "w": point.pose.orientation.w,
            },
        },
        "navigation": {
            "target_pose_radius": point.navigation.target_pose_radius,
            "pnc_mode": point.navigation.pnc_mode,
            "max_forward_speed": point.navigation.max_forward_speed,
            "task_type": point.navigation.task_type,
        },
        "ui": {
            "pixel_u": point.ui.pixel_u,
            "pixel_v": point.ui.pixel_v,
            "heading_pixel_u": point.ui.heading_pixel_u,
            "heading_pixel_v": point.ui.heading_pixel_v,
        },
        "voice": {
            "enabled": point.voice.enabled,
            "display_name": point.voice.display_name,
        },
        "business": point.business.to_dict(),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="robot_local_navigation_v3 map label server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8002)
    parser.add_argument(
        "--config-json",
        default=str(Path.home() / "robot_local_navigation_v3" / "unified_nav_config.json"),
    )
    parser.add_argument(
        "--default-map-root",
        default="/agibot/data/var/MapManagerModule/",
    )
    parser.add_argument(
        "--app-home",
        default=str(Path.home() / "robot_local_navigation_v3"),
    )
    parser.add_argument("--bridge-host", default="0.0.0.0")
    parser.add_argument("--bridge-port", type=int, default=18082)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_store = UnifiedConfigStore(args.config_json)
    if not config_store.exists():
        config_store.save(UnifiedNavConfig())
    app_home = Path(args.app_home).expanduser()
    point_store = MapPointStore(app_home / "maps")
    config = config_store.load()
    if point_store.migrate_from_unified(config):
        config_store.save(config)
    bridge_manager = BridgeServiceManager(
        config_path=config_store.file_path,
        app_home=app_home,
        bridge_host=args.bridge_host,
        bridge_port=args.bridge_port,
    )
    relocalization_manager = RelocalizationTaskManager(app_home=app_home)
    V3LabelRequestHandler.app = V3LabelApp(
        config_store,
        point_store,
        bridge_manager,
        relocalization_manager,
    )
    V3LabelRequestHandler.default_map_root = args.default_map_root
    httpd = ThreadingHTTPServer((args.host, args.port), V3LabelRequestHandler)
    print(
        f"map_label_server_v3 started on http://{args.host}:{args.port}, "
        f"config={config_store.file_path}, bridge={bridge_manager.base_url}",
        flush=True,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    main()
