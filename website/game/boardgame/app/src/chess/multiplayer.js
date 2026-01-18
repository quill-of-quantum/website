/*
 * Copyright 2018 The boardgame.io Authors.
 *
 * Use of this source code is governed by a MIT-style
 * license that can be found in the LICENSE file or at
 * https://opensource.org/licenses/MIT.
 */

import React from 'react';
import { Client } from 'boardgame.io/react';
import { SocketIO } from 'boardgame.io/multiplayer';
import ChessGame from './game';
import ChessBoard from './board';

const getMatchID = () => {
  const hash = window.location.hash || '';
  const query = hash.split('?')[1] || '';
  const params = new URLSearchParams(query);
  return params.get('room') || 'multi';
};

const serverUrl = window.location.origin;
const socketOpts = { path: '/bgio/socket.io' };
const App = Client({
  game: ChessGame,
  board: ChessBoard,
  multiplayer: SocketIO({ server: serverUrl, socketOpts }),
  debug: true,
});

const Multiplayer = (playerID) => () => (
  <div style={{ padding: 50 }}>
    <App matchID={getMatchID()} playerID={playerID} />
    当前身份：{playerID === '0' ? '白方' : playerID === '1' ? '黑方' : '观战'}
  </div>
);

export default Multiplayer;
