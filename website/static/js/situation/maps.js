(function(global) {
  const STORAGE_KEY = 'situation-map-source';
  const DEFAULT_SOURCE = 'amap';
  const SOURCES = Object.freeze({
    amap: Object.freeze({
      id: 'amap',
      label: '高德地图',
      url: 'https://wprd01.is.autonavi.com/appmaptile?x={x}&y={y}&z={z}&style=7',
      attribution: '高德地图',
      coordinateSystem: 'gcj02'
    }),
    osm: Object.freeze({
      id: 'osm',
      label: 'OpenStreetMap',
      url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
      attribution: '&copy; OpenStreetMap contributors',
      coordinateSystem: 'wgs84'
    })
  });

  function isOutsideChina(lat, lng) {
    return lng < 72.004 || lng > 137.8347 || lat < 0.8293 || lat > 55.8271;
  }

  function transformLatitude(lngOffset, latOffset) {
    let result = -100 + 2 * lngOffset + 3 * latOffset
      + 0.2 * latOffset * latOffset
      + 0.1 * lngOffset * latOffset
      + 0.2 * Math.sqrt(Math.abs(lngOffset));
    result += (20 * Math.sin(6 * lngOffset * Math.PI)
      + 20 * Math.sin(2 * lngOffset * Math.PI)) * 2 / 3;
    result += (20 * Math.sin(latOffset * Math.PI)
      + 40 * Math.sin(latOffset / 3 * Math.PI)) * 2 / 3;
    result += (160 * Math.sin(latOffset / 12 * Math.PI)
      + 320 * Math.sin(latOffset * Math.PI / 30)) * 2 / 3;
    return result;
  }

  function transformLongitude(lngOffset, latOffset) {
    let result = 300 + lngOffset + 2 * latOffset
      + 0.1 * lngOffset * lngOffset
      + 0.1 * lngOffset * latOffset
      + 0.1 * Math.sqrt(Math.abs(lngOffset));
    result += (20 * Math.sin(6 * lngOffset * Math.PI)
      + 20 * Math.sin(2 * lngOffset * Math.PI)) * 2 / 3;
    result += (20 * Math.sin(lngOffset * Math.PI)
      + 40 * Math.sin(lngOffset / 3 * Math.PI)) * 2 / 3;
    result += (150 * Math.sin(lngOffset / 12 * Math.PI)
      + 300 * Math.sin(lngOffset / 30 * Math.PI)) * 2 / 3;
    return result;
  }

  function wgs84ToGcj02(lat, lng) {
    if (isOutsideChina(lat, lng)) return [lat, lng];

    const earthRadius = 6378245;
    const eccentricitySquared = 0.006693421622965943;
    const latitudeRadians = lat / 180 * Math.PI;
    const sinLatitude = Math.sin(latitudeRadians);
    const magic = 1 - eccentricitySquared * sinLatitude * sinLatitude;
    const sqrtMagic = Math.sqrt(magic);
    const lngOffset = lng - 105;
    const latOffset = lat - 35;
    const latitudeDelta = transformLatitude(lngOffset, latOffset) * 180
      / ((earthRadius * (1 - eccentricitySquared)) / (magic * sqrtMagic) * Math.PI);
    const longitudeDelta = transformLongitude(lngOffset, latOffset) * 180
      / (earthRadius / sqrtMagic * Math.cos(latitudeRadians) * Math.PI);

    return [lat + latitudeDelta, lng + longitudeDelta];
  }

  function getSource(sourceId) {
    return SOURCES[sourceId] || SOURCES[DEFAULT_SOURCE];
  }

  function getSelectedSource() {
    try {
      return getSource(global.localStorage.getItem(STORAGE_KEY)).id;
    } catch (error) {
      return DEFAULT_SOURCE;
    }
  }

  function setSelectedSource(sourceId) {
    const selected = getSource(sourceId).id;
    try {
      global.localStorage.setItem(STORAGE_KEY, selected);
    } catch (error) {
      // localStorage may be unavailable in privacy-restricted browsers.
    }
    return selected;
  }

  function projectPoint(lat, lng, sourceId) {
    const source = getSource(sourceId);
    return source.coordinateSystem === 'gcj02' ? wgs84ToGcj02(lat, lng) : [lat, lng];
  }

  function createTileLayer(sourceId, options) {
    const source = getSource(sourceId);
    return global.L.tileLayer(source.url, Object.assign({
      maxZoom: 19,
      attribution: source.attribution
    }, options || {}));
  }

  global.SituationMaps = Object.freeze({
    sources: SOURCES,
    getSource,
    getSelectedSource,
    setSelectedSource,
    projectPoint,
    createTileLayer
  });
})(window);
