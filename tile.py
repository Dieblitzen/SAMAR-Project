## Data processising

import numpy as np
import get_bounding_boxes
import visualize
from sklearn.feature_extraction import image

## Gets the bounding boxes per tile, with centre relative to tile coordinates.
def boxes_in_tile(bboxes, row_start, row_end, col_start, col_end):

    bboxes_in_tile = []

    for i in range(len(bboxes)):
        centreX = bboxes[i][0]
        centreY = bboxes[i][1]

        if (row_start <= centreX < row_end) and (col_start <= centreY < col_end):

            # Changing bbox centre to be relative to tile
            bboxes[i][0] -= row_start
            bboxes[i][1] -= col_start

            # Mutating bboxes to reduce loop time after getting each set of bboxes per tile. 
            bboxes_in_tile.append(bboxes.pop(i))

    return bboxes_in_tile



## Takes array representing entire queried image and bounding boxes (with pixel coordinates) relative to 
## entire image, and outputs a list of tuples where the first element is the tiled image and the second
## element is the list of bounding boxes with coordinates relative to the tile. 
def tile_image(entire_img, b_boxes, tile_size):

    num_rows, num_cols, depth = entire_img.shape 

    output = []

    for row in range(num_rows//tile_size):
      for col in range(num_cols//tile_size): 

        row_start = row*tile_size 
        row_end = (row+1)*tile_size  

        col_start = col*tile_size
        col_end = (col+1)*tile_size
        
        # row_end and col_end is not included in indexing, because array indexing is not end inclusive. 
        tile = entire_img[row_start:row_end, col_start:col_end, :]
        bboxes_in_tile = boxes_in_tile(b_boxes, row_start, row_end, col_start, col_end)


        output.append((tile, bboxes_in_tile))
    
    return output

    # tiled_images = image.extract_patches_2d(entire_image, (tile_size, tile_size))
    # return tiled_images
