"""Test input for Sprint 4: slide text layers + transcript lines for four lectures.

Deck PAGE IMAGES are the standard synthetic slides (so slide-change detection runs for real); only the PDF text
layer and the transcript lines differ per lecture. Transcript lines are explicit test INPUT (no speech model offline),
exactly as in Sprints 1-3. Nothing here is product data.
"""
from core.lecture.align import Segment

# ── Deep Learning I (course "Deep Learning") — slides change at 6 / 15 / 20 s ──
DL1_SLIDES = [
    ["Gradient Descent", "Update rule: w = w - lr * gradient", "Loss: L = (1/n) * sum((y - y_hat)^2)"],
    ["Backpropagation", "Chain rule: dL/dw = dL/dy * dy/dw"],
    ["Regularization", "Weight decay: L_total = L + lambda * ||w||^2", "Dropout rate p = 0.5"],
    ["Convolutions", "Output size = (n - k + 2p) / s + 1"],
]
DL1 = [
    Segment(0.5, 3.0, "A gradient is defined as the vector of partial derivatives of the loss with respect to the weights."),
    Segment(3.0, 5.8, "The learning rate means the size of the step we take downhill on each update."),
    Segment(6.5, 9.0, "Backpropagation is the algorithm that computes gradients by applying the chain rule layer by layer."),
    Segment(9.0, 11.0, "For example, in a two layer network the gradient of the first layer depends on the second layer."),
    Segment(11.0, 13.0, "Be careful, if the learning rate is too large then training can diverge."),
    Segment(13.0, 14.8, "This is important: vanishing gradients appear when many small derivatives are multiplied together."),
    Segment(15.5, 17.0, "Overfitting happens when a model memorizes the training data instead of learning general patterns."),
    Segment(17.0, 18.0, "Regularization means adding a penalty on large weights to the loss."),
    Segment(18.0, 19.0, "Remember that dropout randomly turns off 50 percent of the neurons during training."),
    Segment(19.0, 19.9, "Weight decay reduces overfitting because it keeps the weights small."),
    Segment(20.5, 22.0, "A convolution is an operation that slides a small kernel across the image."),
    Segment(22.0, 24.0, "For instance, an edge detector kernel responds strongly to vertical lines."),
    Segment(24.0, 25.2, "A common mistake is to apply dropout at test time."),
    Segment(25.2, 26.6, "The exam will cover pooling layers."),
]

# ── Deep Learning II (same course) — revisits "learning rate" ──
DL2_SLIDES = [
    ["Learning Rate Schedules", "Step decay: lr_t = lr_0 * 0.5^(t/10)"],
    ["Batch Normalization", "Normalize: y = gamma * x_hat + beta"],
    ["Momentum", "Update: v = beta * v + gradient"],
    ["Adam", "Adam combines momentum and adaptive learning rates"],
]
DL2 = [
    Segment(0.5, 3.0, "The learning rate means the size of each parameter update, and a schedule lowers it over time."),
    Segment(3.0, 5.8, "For example, step decay halves the learning rate every ten epochs."),
    Segment(6.5, 9.0, "Batch normalization is a technique that normalizes the inputs of each layer."),
    Segment(9.0, 14.5, "Remember that batch normalization makes training faster because activations stay in a similar range."),
    Segment(15.5, 18.0, "Momentum is a method that accumulates an exponentially decaying average of past gradients."),
    Segment(18.0, 19.9, "Be careful, too much momentum can overshoot the minimum."),
    Segment(20.5, 24.0, "Adam is an optimizer that combines momentum with adaptive learning rates."),
    Segment(24.0, 26.5, "The exam will cover Adam and momentum."),
]

# ── Relational Databases (a DIFFERENT course) ──
DB_SLIDES = [
    ["Primary Keys", "Rule: every row has a unique key"],
    ["Foreign Keys", "Constraint: child.parent_id = parent.id"],
    ["Inner Join", "SELECT * FROM a JOIN b ON a.id = b.a_id"],
    ["Normalization", "Third normal form removes transitive dependencies"],
]
DB = [
    Segment(0.5, 3.5, "A primary key is a column that uniquely identifies each row in a table."),
    Segment(3.5, 5.8, "Remember that a primary key can never be empty."),
    Segment(6.5, 10.0, "A foreign key is a column that references the primary key of another table."),
    Segment(10.0, 14.5, "Be careful, deleting a parent row can break rows that still reference it."),
    Segment(15.5, 19.5, "An inner join returns only the rows that match in both tables."),
    Segment(20.5, 24.5, "Normalization means organizing tables to reduce duplicated data."),
    Segment(24.5, 26.5, "The exam will cover third normal form."),
]
