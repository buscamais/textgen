import logging
import os

from book_corpus import BookCorpusDataset
from parser import parser
from preprocess import preprocess_data_vocab
from train import train
from train_helper import Network
from utils import Config, set_logger, prepare_paths


log = logging.getLogger('main')

if __name__ == '__main__':
    # Parsing arguments and set configs
    args = parser.parse_args()
    cfg = Config(vars(args))

    # Set all the paths
    prepare_paths(cfg)

    # Logger
    set_logger(cfg)
    log = logging.getLogger('main')

    # Preprocessing
    vocab = preprocess_data_vocab(cfg)

    # Load dataset
    book_corpus = BookCorpusDataset(cfg.data_filepath)

    # Train
    net = Network(cfg, book_corpus, vocab)
    train(net)

    #trainer = Trainer(cfg=cfg, vocab=vocab, data_loader=data_loader)
    import ipdb; ipdb.set_trace()
    ddd = batchify(data_loader)
    iter(data_loader).__next__()
    log.info('prepare_data_and_vocab')
