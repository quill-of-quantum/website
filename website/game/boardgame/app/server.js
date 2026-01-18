/*
 * Copyright 2017 The boardgame.io Authors
 *
 * Use of this source code is governed by a MIT-style
 * license that can be found in the LICENSE file or at
 * https://opensource.org/licenses/MIT.
 */

import serverPkg from 'boardgame.io/dist/cjs/server.js';
import TicTacToe from './src/tic-tac-toe/game.js';
import Chess from './src/chess/game.js';

const { Server, Origins, SocketIO } = serverPkg;

const PORT = process.env.PORT || 8000;
const server = Server({
  games: [TicTacToe, Chess],
  origins: [Origins.ANY],
  transport: new SocketIO({ socketOpts: { path: '/bgio/socket.io' } }),
});
server.run(PORT, () => {
  console.log(`Serving at: http://localhost:${PORT}`);
});
