/*
 * Copyright 2018 The boardgame.io Authors
 *
 * Use of this source code is governed by a MIT-style
 * license that can be found in the LICENSE file or at
 * https://opensource.org/licenses/MIT.
 */

import Chess from 'chess.js';
import * as corePkg from 'boardgame.io/dist/cjs/core.js';
const INVALID_MOVE = corePkg.INVALID_MOVE;

// Helper to instantiate chess.js correctly on
// both browser and Node.
function Load(pgn) {
  let chess = null;
  if (Chess.Chess) {
    chess = new Chess.Chess();
  } else {
    chess = new Chess();
  }
  chess.load_pgn(pgn);
  return chess;
}

function getWinner(chess) {
  const board = chess.board();
  let hasWhiteKing = false;
  let hasBlackKing = false;
  for (const row of board) {
    for (const piece of row) {
      if (!piece) continue;
      if (piece.type === 'k') {
        if (piece.color === 'w') hasWhiteKing = true;
        if (piece.color === 'b') hasBlackKing = true;
      }
    }
  }
  if (!hasWhiteKing && hasBlackKing) return 'b';
  if (!hasBlackKing && hasWhiteKing) return 'w';
  return null;
}

const ChessGame = {
  name: 'chess',

  setup: () => ({ pgn: '', winner: null }),

  moves: {
    move({ G, playerID }, move) {
      if (G.winner) return INVALID_MOVE;
      const chess = Load(G.pgn);
      const normalizedPlayerID = playerID == null ? null : String(playerID);
      if (normalizedPlayerID !== '0' && normalizedPlayerID !== '1') {
        return INVALID_MOVE;
      }
      const expectedPlayer = chess.turn() === 'w' ? '0' : '1';
      if (normalizedPlayerID !== expectedPlayer) return INVALID_MOVE;
      const moveInput = { ...move };
      if (!moveInput.promotion && moveInput.from && moveInput.to) {
        const piece = chess.get(moveInput.from);
        const targetRank = moveInput.to.slice(1);
        if (
          piece &&
          piece.type === 'p' &&
          ((piece.color === 'w' && targetRank === '8') ||
            (piece.color === 'b' && targetRank === '1'))
        ) {
          moveInput.promotion = 'q';
        }
      }
      const result = chess.move(moveInput, { legal: false });
      if (!result) return INVALID_MOVE;
      const winner = getWinner(chess);
      return { pgn: chess.pgn(), winner };
    },
  },

};

export default ChessGame;
