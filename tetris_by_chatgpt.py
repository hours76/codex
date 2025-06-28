import pygame
import random
import sys

# --- Constants ---
GRID_WIDTH, GRID_HEIGHT = 10, 20
BLOCK_SIZE = 30
PLAY_WIDTH = GRID_WIDTH * BLOCK_SIZE
PLAY_HEIGHT = GRID_HEIGHT * BLOCK_SIZE
WINDOW_WIDTH = PLAY_WIDTH + 200
WINDOW_HEIGHT = PLAY_HEIGHT
FPS = 60
MOVE_DOWN_EVENT = pygame.USEREVENT + 1
DROP_SPEED_MS = 500  # piece falls every 500 ms

# Colors (R, G, B)
BLACK  = (  0,   0,   0)
WHITE  = (255, 255, 255)
GREY   = (128, 128, 128)

COLORS = {
    'I': (0, 240, 240),
    'O': (240, 240, 0),
    'T': (160, 0, 240),
    'S': (0, 240, 0),
    'Z': (240, 0, 0),
    'J': (0, 0, 240),
    'L': (240, 160, 0)
}

# Tetromino definitions (list of rotations, each rotation a list of (x,y) offsets)
SHAPES = {
    'I': [ [(0,0),(1,0),(2,0),(3,0)],
           [(1,-1),(1,0),(1,1),(1,2)] ],
    'O': [ [(0,0),(1,0),(0,1),(1,1)] ],
    'T': [ [(1,0),(0,1),(1,1),(2,1)],
           [(1,0),(1,1),(2,1),(1,2)],
           [(0,1),(1,1),(2,1),(1,2)],
           [(1,0),(0,1),(1,1),(1,2)] ],
    'S': [ [(1,0),(2,0),(0,1),(1,1)],
           [(1,0),(1,1),(2,1),(2,2)] ],
    'Z': [ [(0,0),(1,0),(1,1),(2,1)],
           [(2,0),(1,1),(2,1),(1,2)] ],
    'J': [ [(0,0),(0,1),(1,1),(2,1)],
           [(1,0),(2,0),(1,1),(1,2)],
           [(0,1),(1,1),(2,1),(2,2)],
           [(1,0),(1,1),(1,2),(0,2)] ],
    'L': [ [(2,0),(0,1),(1,1),(2,1)],
           [(1,0),(1,1),(1,2),(2,2)],
           [(0,1),(1,1),(2,1),(0,2)],
           [(0,0),(1,0),(1,1),(1,2)] ]
}

class Piece:
    def __init__(self, x, y, shape_key):
        self.x = x
        self.y = y
        self.shape_key = shape_key
        self.rot = 0  # rotation index

    @property
    def shape(self):
        return SHAPES[self.shape_key][self.rot]

    def rotate(self):
        self.rot = (self.rot + 1) % len(SHAPES[self.shape_key])

def create_grid(locked):
    grid = [[None for _ in range(GRID_WIDTH)] for _ in range(GRID_HEIGHT)]
    for (x, y), color in locked.items():
        if y >= 0:
            grid[y][x] = color
    return grid

def valid_space(piece, grid):
    for x_off, y_off in piece.shape:
        x = piece.x + x_off
        y = piece.y + y_off
        if x < 0 or x >= GRID_WIDTH or y >= GRID_HEIGHT:
            return False
        if y >= 0 and grid[y][x]:
            return False
    return True

def clear_rows(grid, locked):
    cleared = 0
    for y in range(GRID_HEIGHT-1, -1, -1):
        if all(grid[y][x] for x in range(GRID_WIDTH)):
            cleared += 1
            # remove from locked
            for x in range(GRID_WIDTH):
                locked.pop((x,y))
            # shift rows down
            for (lx, ly) in sorted(list(locked.keys()), key=lambda k: k[1]):
                if ly < y:
                    color = locked.pop((lx,ly))
                    locked[(lx, ly+1)] = color
    return cleared

def check_lost(locked):
    return any(ly < 0 for (_,ly) in locked.keys())

def draw_grid(surface, grid):
    for y in range(GRID_HEIGHT):
        pygame.draw.line(surface, GREY, (0, y*BLOCK_SIZE), (PLAY_WIDTH, y*BLOCK_SIZE))
        for x in range(GRID_WIDTH):
            pygame.draw.line(surface, GREY, (x*BLOCK_SIZE, 0), (x*BLOCK_SIZE, PLAY_HEIGHT))

def draw_window(win, grid, score):
    win.fill(BLACK)
    # draw play area
    for y in range(GRID_HEIGHT):
        for x in range(GRID_WIDTH):
            color = grid[y][x]
            if color:
                pygame.draw.rect(win, color,
                                 (x*BLOCK_SIZE, y*BLOCK_SIZE, BLOCK_SIZE, BLOCK_SIZE))
    draw_grid(win, grid)
    # score
    font = pygame.font.SysFont('Arial', 24)
    score_surf = font.render(f"Score: {score}", True, WHITE)
    win.blit(score_surf, (PLAY_WIDTH + 20, 30))
    pygame.display.update()

def get_new_piece():
    key = random.choice(list(SHAPES.keys()))
    # spawn near top center
    return Piece(GRID_WIDTH//2 - 2, -2, key)

def main():
    pygame.init();
    win = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT))
    pygame.display.set_caption("Tetris (simple)")
    clock = pygame.time.Clock()
    pygame.time.set_timer(MOVE_DOWN_EVENT, DROP_SPEED_MS)

    locked_positions = {}
    grid = create_grid(locked_positions)
    current_piece = get_new_piece()
    next_piece = get_new_piece()
    score = 0
    fall_time = 0

    running = True
    while running:
        clock.tick(FPS)
        grid = create_grid(locked_positions)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
                break
            if event.type == MOVE_DOWN_EVENT:
                # move down
                current_piece.y += 1
                if not valid_space(current_piece, grid):
                    current_piece.y -= 1
                    # lock piece
                    for x_off, y_off in current_piece.shape:
                        pos = (current_piece.x + x_off, current_piece.y + y_off)
                        locked_positions[pos] = COLORS[current_piece.shape_key]
                    current_piece = next_piece
                    next_piece = get_new_piece()
                    score += clear_rows(grid, locked_positions) * 100
                    if check_lost(locked_positions):
                        running = False
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_LEFT:
                    current_piece.x -= 1
                    if not valid_space(current_piece, grid):
                        current_piece.x += 1
                elif event.key == pygame.K_RIGHT:
                    current_piece.x += 1
                    if not valid_space(current_piece, grid):
                        current_piece.x -= 1
                elif event.key == pygame.K_DOWN:
                    current_piece.y += 1
                    if not valid_space(current_piece, grid):
                        current_piece.y -= 1
                elif event.key == pygame.K_UP:
                    current_piece.rotate()
                    if not valid_space(current_piece, grid):
                        # undo rotate
                        current_piece.rotate()
                        current_piece.rotate()
                        current_piece.rotate()
                elif event.key == pygame.K_SPACE:
                    # hard drop
                    while valid_space(current_piece, grid):
                        current_piece.y += 1
                    current_piece.y -= 1
        # draw current piece on grid for rendering
        for x_off, y_off in current_piece.shape:
            x = current_piece.x + x_off
            y = current_piece.y + y_off
            if y >= 0:
                grid[y][x] = COLORS[current_piece.shape_key]

        draw_window(win, grid, score)

    # Game over
    font = pygame.font.SysFont('Arial', 48)
    over_surf = font.render("Game Over", True, WHITE)
    win.blit(over_surf, (PLAY_WIDTH//2 - over_surf.get_width()//2, PLAY_HEIGHT//2 - 24))
    pygame.display.update()
    pygame.time.delay(3000)
    pygame.quit()
    sys.exit()

if __name__ == "__main__":
    main()
