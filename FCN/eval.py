import matplotlib.pyplot as plt

with open("./ImSegEval.log") as file_name:
  ma_train_token = "Moving Average Training Loss: "
  ma_val_token = "Moving Average Validation Loss: "
  iou_token = "Epoch IoU: "
  
  ma_training_loss = []
  ma_val_loss = []
  iou_scores = []

  for line in file_name: 
    ma_train_idx = line.find(ma_train_token) 
    ma_val_idx = line.find(ma_val_token)
    iou_idx = line.find(iou_token)

    if ma_train_idx != -1:
      ma_train_idx = ma_train_idx + len(ma_train_token)
      train_loss = float(line[ma_train_idx:].strip())
      ma_training_loss.append(train_loss)
    elif ma_val_idx != -1: 
      ma_val_idx = ma_val_idx + len(ma_val_token)
      val_loss = float(line[ma_val_idx:].strip())
      ma_val_loss.append(val_loss)
    elif iou_idx != -1: 
      iou_idx = iou_idx + len(iou_token)
      iou = float(line[iou_idx:].strip())
      iou_scores.append(iou)
    

  epoch_num = list(range(1, len(ma_training_loss) + 1))
  # Training vs Validation Loss graph
  plt.plot(epoch_num, ma_training_loss)
  plt.plot(epoch_num, ma_val_loss)

  plt.show()

  # Iou scores graph
  plt.plot(epoch_num, iou_scores)

  plt.show()
  

    