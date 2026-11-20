import assert from "node:assert/strict";
import test from "node:test";
import { getWuhanBoundary, getWuhanBounds, isInsideWuhan, loadWuhanBoundary } from "../src/lib/wuhanBoundary.ts";

test("本地 Wuhan MultiPolygon 覆盖武汉全域并处理内环和非法坐标", async () => {
  const loaded = await loadWuhanBoundary();
  assert.equal(loaded.divisionCode, "420100");
  assert.equal(loaded.coordinateSystem, "GCJ-02");
  assert.equal(loaded.polygons.length, 3, "保留独立外环");
  assert.equal(loaded.polygons.reduce((count, polygon) => count + polygon.length - 1, 0), 3, "保留内环");
  assert.equal(getWuhanBoundary(), loaded, "边界来自模块内静态数据");

  for (const [name, point] of Object.entries({
    黄鹤楼: { lng: 114.3109408, lat: 30.5424732 },
    东湖: { lng: 114.4165371, lat: 30.547707 },
    江汉路: { lng: 114.2974273, lat: 30.5775643 },
    黄陂: { lng: 114.3795673, lat: 30.8788514 },
    新洲: { lng: 114.8062287, lat: 30.8396211 },
    江夏: { lng: 114.3254458, lat: 30.3475519 },
    蔡甸: { lng: 114.0356768, lat: 30.5776159 },
  })) assert.equal(isInsideWuhan(point), true, `${name} 应在武汉范围内`);

  assert.equal(isInsideWuhan({ lng: 114.172, lat: 22.298 }), false, "香港尖沙咀应在范围外");
  assert.equal(isInsideWuhan({ lng: 116.397, lat: 39.908 }), false, "北京天安门应在范围外");
  assert.equal(isInsideWuhan({ lng: 121.4737, lat: 31.2304 }), false, "上海外滩应在范围外");
  assert.equal(isInsideWuhan({ lng: 114.6359883, lat: 30.569125 }), false, "内环应视为范围外");
  assert.equal(isInsideWuhan({ lng: Number.NaN, lat: 30.5 }), false);
  assert.equal(isInsideWuhan({ lng: Infinity, lat: 30.5 }), false);
  assert.equal(isInsideWuhan(undefined), false);
  const boundaryPoint = loaded.rings[0][0];
  assert.equal(isInsideWuhan({ lng: boundaryPoint[0], lat: boundaryPoint[1] }), true, "行政边界上的点应合法");

  const bounds = getWuhanBounds();
  assert(bounds && bounds.west < 114 && bounds.east > 115 && bounds.south < 30 && bounds.north > 31);
});
